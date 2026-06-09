"""Перевыделение концептов локальной моделью (Ollama/MLX) с голосом Ани.

Отличия от старого scripts/extract_concepts_via_claude.py:
  - Новая структура: name + description(поиск) + quotes(ДОСЛОВНО, голос) + salience(вес).
  - Спикер-фильтр: цитаты берутся ТОЛЬКО из реплик Ани (data/speakers.json).
  - Бэкенд — любой OpenAI-совместимый эндпоинт (по умолчанию локальная Ollama),
    задаётся env EXTRACT_API_URL / EXTRACT_MODEL / EXTRACT_API_KEY.
  - Лекция обрабатывается ОКНАМИ блоков (контекст модели ограничен).
  - Валидатор: каждая цитата должна (нечётко) присутствовать в тексте Ани в её блоках.
  - Пишет concepts_v2.json (старый concepts.json НЕ трогает — для сравнения old/new).

Запуск (модель должна быть поднята: `ollama serve` + `ollama pull gemma2:27b`):
    # пилот на конкретных лекциях:
    python3 scripts/extract_concepts_local.py "КП. Гаджеты ч1" "Кофе с психологом. Тревожность"
    # все:
    python3 scripts/extract_concepts_local.py
    # другая модель/бэкенд:
    EXTRACT_MODEL=qwen2.5:32b python3 scripts/extract_concepts_local.py ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

API_URL = os.getenv("EXTRACT_API_URL", "http://localhost:11434/v1/chat/completions")
MODEL = os.getenv("EXTRACT_MODEL", "gemma2:27b")
API_KEY = os.getenv("EXTRACT_API_KEY", "ollama")  # Ollama игнорирует, но поле нужно
TRANSCRIPTS = Path("data/transcripts")
SPEAKERS_PATH = Path("data/speakers.json")

# Символьный бюджет одного окна (Russian ≈ 3-4 симв/токен; держим вход ~<5k токенов).
WINDOW_CHARS = 11000
# Порог нечёткого совпадения цитаты с текстом Ани.
QUOTE_MATCH_MIN = 0.82

VALID_TYPES = frozenset({
    "term", "technique", "example", "claim", "recommendation",
    "exercise", "warning", "question", "metaphor",
})

ANNA = "[АНЯ]"
OTHER = "[—]"

PROMPT_TEMPLATE = """Ты обрабатываешь фрагмент лекции психолога Анны для базы знаний её метода.

Извлеки концепты — названные единицы знания её метода. Используй РОВНО эти 9 типов:
term (терминология метода), technique (приёмы/ходы), example (кейсы/истории),
claim (утверждения/принципы), recommendation (книги/авторы/ресурсы),
exercise (практики/упражнения), warning (предостережения/red flags),
question (фирменные вопросы для рефлексии), metaphor (метафоры/образы).

Для КАЖДОГО концепта верни:
- name: короткое имя, 3-7 слов.
- type: один из 9 типов.
- description: 1-2 предложения О СМЫСЛЕ концепта, аналитично — это для ПОИСКА, НЕ цитата.
  Пиши СТРОГО ПО-РУССКИ, без английских слов.
- quotes: массив из 1-3 ДОСЛОВНЫХ фрагментов речи Ани, иллюстрирующих концепт её голосом.
  Копируй СЛОВО В СЛОВО только из строк, помеченных {anna}. Не перефразируй, не чисти,
  не сокращай, не исправляй. Слова других людей (строки {other}) НЕ цитировать.
- salience: значимость 1-3. 3 = центральный тезис, вокруг которого строится разговор;
  2 = важная самостоятельная мысль; 1 = проходное упоминание.
- source_block_indices: номера блоков (из заголовков "=== Блок N ==="), откуда взято.

Правила:
- description — про смысл (для поиска); quotes — живые слова Ани (для голоса). НЕ путать.
- Бери характерное ДЛЯ ЕЁ МЕТОДА, не общеизвестное («коммуникация важна» — не брать).
- НЕ ПРОПУСКАЙ метафоры, фирменные вопросы и приёмы — это самое ценное в её голосе.
- salience=3 ставь РЕДКО — только действительно центральным тезисам метода; в большинстве
  фрагментов таких нет, ставь 1 или 2. Не делай почти всё «тройками».
- Если в фрагменте нет концептов — верни пустой массив.

Верни СТРОГО JSON-объект вида:
{{"concepts": [{{"name": "...", "type": "claim", "description": "...",
  "quotes": ["...", "..."], "salience": 2, "source_block_indices": [3]}}]}}

Фрагмент лекции:
{blocks}
"""


# ─── Рендер блоков со спикер-разметкой ────────────────────────────────────────

def _norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def render_blocks(raw: dict, segments: list[dict], anna_label: str | None):
    """Список (block_index, block_text_for_prompt, anna_text_norm)."""
    raw_segs = raw.get("segments", [])
    blocks = []
    for i, b in enumerate(segments, 1):
        s, e = float(b["start_ts"]), float(b["end_ts"])
        sm, ss = divmod(int(s), 60)
        em, es = divmod(int(e), 60)
        lines: list[str] = []
        anna_parts: list[str] = []
        for ws in raw_segs:
            wstart, wend = float(ws.get("start", 0)), float(ws.get("end", 0))
            if wend <= s or wstart >= e:
                continue
            text = (ws.get("text") or "").strip()
            if not text:
                continue
            is_anna = anna_label is None or ws.get("speaker") == anna_label
            tag = ANNA if is_anna else OTHER
            lines.append(f"{tag} {text}")
            if is_anna:
                anna_parts.append(text)
        header = f"=== Блок {i} ===\nЗаголовок: {b.get('title','')}\nВремя: {sm}:{ss:02d}–{em}:{es:02d}\n"
        blocks.append((i, header + "\n".join(lines) + "\n", _norm(" ".join(anna_parts))))
    return blocks


def windows(blocks, budget=WINDOW_CHARS):
    """Группировать блоки в окна по символьному бюджету."""
    cur, cur_len = [], 0
    for blk in blocks:
        blen = len(blk[1])
        if cur and cur_len + blen > budget:
            yield cur
            cur, cur_len = [], 0
        cur.append(blk)
        cur_len += blen
    if cur:
        yield cur


# ─── Вызов модели ─────────────────────────────────────────────────────────────

def call_model(blocks_text: str, *, timeout: int = 600) -> dict:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT_TEMPLATE.format(
            blocks=blocks_text, anna=ANNA, other=OTHER)}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return _loads_lenient(content)


def _loads_lenient(content: str) -> dict:
    """Терпимый парс: модель иногда добавляет прозу вокруг JSON или усекает массив."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", content, re.DOTALL)  # внешний объект
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    cut = content.rfind("},")  # усечённый массив → закрыть по последнему полному объекту
    if cut != -1:
        try:
            return json.loads(content[: cut + 1] + "]}")
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("unrepairable model output", content, 0)


# ─── Валидация ────────────────────────────────────────────────────────────────

def quote_matches(quote: str, anna_text_norm: str) -> bool:
    """Цитата (нечётко) присутствует в речи Ани соответствующих блоков."""
    q = _norm(quote)
    if not q:
        return False
    if q in anna_text_norm:
        return True
    m = SequenceMatcher(None, q, anna_text_norm).find_longest_match(0, len(q), 0, len(anna_text_norm))
    return (m.size / len(q)) >= QUOTE_MATCH_MIN


_TAG_RE = re.compile(r"^\s*\[[^\]]*\]\s*")


def validate(concepts: list[dict], full_anna_norm: str) -> tuple[list[dict], dict]:
    # Дословность проверяем по ВСЕЙ речи Ани в лекции: source_block_indices от модели
    # неточны, и цитата может быть из соседнего блока — это всё равно слова Ани.
    out, stats = [], {"dropped_type": 0, "dropped_no_quote": 0, "quotes_total": 0, "quotes_bad": 0}
    for c in concepts:
        if c.get("type") not in VALID_TYPES:
            stats["dropped_type"] += 1
            continue
        good_quotes = []
        for q in (c.get("quotes") or []):
            stats["quotes_total"] += 1
            if not isinstance(q, str):
                stats["quotes_bad"] += 1
                continue
            q = _TAG_RE.sub("", q).strip()  # срезать протёкший тег [АНЯ]/[—]
            if quote_matches(q, full_anna_norm):
                good_quotes.append(q)
            else:
                stats["quotes_bad"] += 1
        if not good_quotes:
            stats["dropped_no_quote"] += 1
            continue
        sal = c.get("salience")
        c["salience"] = sal if isinstance(sal, int) and 1 <= sal <= 3 else 2
        c["quotes"] = good_quotes
        out.append(c)
    return out, stats


# ─── Обработка лекции ─────────────────────────────────────────────────────────

def process(lecture_dir: Path, anna_label: str | None, *, force: bool) -> tuple[str, str]:
    raw_path = lecture_dir / "raw.json"
    seg_path = lecture_dir / "segments.json"
    out_path = lecture_dir / "concepts_v2.json"
    if not raw_path.exists() or not seg_path.exists():
        return ("error", "нет raw.json или segments.json")
    if out_path.exists() and not force:
        return ("skipped", str(out_path))
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        segments = json.loads(seg_path.read_text(encoding="utf-8"))
    except Exception as e:
        return ("error", f"парсинг: {type(e).__name__}: {e}")
    if not segments:
        return ("error", "пустой segments.json")

    blocks = render_blocks(raw, segments, anna_label)
    full_anna_norm = " ".join(anna_norm for (_i, _txt, anna_norm) in blocks)

    raw_concepts, all_concepts = [], []
    agg = {"dropped_type": 0, "dropped_no_quote": 0, "quotes_total": 0, "quotes_bad": 0}
    json_errors = 0
    for win in windows(blocks):
        blocks_text = "\n".join(b[1] for b in win)
        try:
            res = call_model(blocks_text)
        except (urllib.error.URLError, TimeoutError) as e:
            return ("error", f"модель недоступна: {e}")
        except json.JSONDecodeError:
            json_errors += 1  # битое окно — пропускаем, не теряем всю лекцию
            continue
        concepts = res.get("concepts", []) if isinstance(res, dict) else []
        raw_concepts.extend(concepts)
        good, stats = validate(concepts, full_anna_norm)
        all_concepts.extend(good)
        for k in agg:
            agg[k] += stats[k]

    # Сырой ответ модели (до валидации) — чтобы тюнить валидатор без повторных прогонов.
    (lecture_dir / "concepts_v2_raw.json").write_text(
        json.dumps({"concepts": raw_concepts, "full_anna_norm": full_anna_norm},
                   ensure_ascii=False), encoding="utf-8")
    out_path.write_text(json.dumps(all_concepts, ensure_ascii=False, indent=2), encoding="utf-8")
    return ("ok", f"{len(all_concepts)} концептов | плохих цитат: {agg['quotes_bad']}/{agg['quotes_total']} "
                  f"| отброшено(тип/без_цитат): {agg['dropped_type']}/{agg['dropped_no_quote']} "
                  f"| битых окон: {json_errors} → {out_path.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lectures", nargs="*", help="имена папок лекций (по умолчанию все)")
    parser.add_argument("--force", action="store_true", help="перезаписать concepts_v2.json")
    args = parser.parse_args()

    speakers = json.loads(SPEAKERS_PATH.read_text(encoding="utf-8")) if SPEAKERS_PATH.exists() else {}

    if args.lectures:
        dirs = [TRANSCRIPTS / name for name in args.lectures]
    else:
        dirs = sorted(p.parent for p in TRANSCRIPTS.glob("*/raw.json"))

    print(f"Модель: {MODEL} | эндпоинт: {API_URL}")
    for d in dirs:
        name = d.name
        sp = speakers.get(name) or {}
        # Спорные лекции: диаризация разбила голос Ани на несколько label
        # (доминанта <60%). Фильтр по одному label потерял бы половину её речи —
        # поэтому НЕ фильтруем (anna=None), берём весь текст. Риск зацепить реплику
        # студента низкий: извлекаются концепты метода, а они идут от лектора.
        anna = None if sp.get("ambiguous") else sp.get("anna")
        try:
            status, info = process(d, anna, force=args.force)
        except Exception as e:
            status, info = "error", f"{type(e).__name__}: {e}"
        marker = {"ok": "[+]", "skipped": "[=]"}.get(status, "[!]")
        print(f"  {marker} {name}: {info}", file=sys.stderr if status == "error" else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
