"""Извлечение концептов из лекций через claude -p (без Anthropic API).

Для каждой пары raw.json + segments.json — рендерит лекцию как пронумерованные
блоки (заголовок + резюме + текст), вызывает claude --print с инструкцией по
9 типам концептов (psy_helper/taxonomy.py), результат пишет в concepts.json.

Идемпотентно: если concepts.json уже есть — пропускает.

Запуск (на хосте):
    python3 scripts/extract_concepts_via_claude.py
    python3 scripts/extract_concepts_via_claude.py "data/transcripts/<имя>/raw.json"
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


PROMPT_TEMPLATE = """Ты обрабатываешь лекцию психолога Анны для базы знаний.

Извлеки концепты — названные единицы знания, которые могут пригодиться для поиска и ответов клиентам.

Используй РОВНО эти 9 типов (НЕ выдумывай новые):

- term: терминология метода (например: «активная позиция в коммуникации»)
- technique: приёмы, ходы, шаги в работе
- example: кейсы, ситуации, истории, иллюстрации
- claim: утверждения, принципы, ценностные установки
- recommendation: книги, фильмы, авторы, ресурсы
- exercise: конкретные практики, упражнения, домашние задания
- warning: предостережения, красные флаги, чего не делать
- question: вопросы для рефлексии, фирменные формулировки
- metaphor: метафоры, образы

Правила:
- Каждый концепт — уникален. Если повторяется в нескольких блоках, source_block_indices содержит несколько чисел.
- name — короткое (3–7 слов на русском); description — 1–2 предложения по делу.
- Будь конкретной, не абстрактной. «Лучше слушать» — плохо. «Активное слушание через отражение содержания» — хорошо.
- Не извлекай общеизвестное (например, «коммуникация важна»). Бери то, что характерно ДЛЯ ЕЁ МЕТОДА.
- Если что-то не подходит ни под один из 9 типов — не включай.

Сделай ровно следующее:
1. Прочитай файл {input_path}. Там пронумерованные блоки лекции (1-based).
2. Извлеки концепты.
3. Запиши результат в файл {output_path} как валидный JSON-массив:
[
  {{
    "name": "...",
    "type": "term|technique|example|claim|recommendation|exercise|warning|question|metaphor",
    "description": "...",
    "source_block_indices": [1, 5]
  }},
  ...
]

После записи файла напиши единственное слово OK и ничего больше.
Если ошибка — FAIL: <причина>.
"""


def render_blocks(raw_data: dict, segments: list[dict]) -> str:
    """Сделать пронумерованный текст блоков для Claude."""
    raw_segments = raw_data.get("segments", [])
    out = []
    for i, b in enumerate(segments, 1):
        s = float(b["start_ts"])
        e = float(b["end_ts"])
        sm, ss = divmod(int(s), 60)
        em, es = divmod(int(e), 60)
        # Текст блока — склейка whisper-сегментов в этом диапазоне
        parts = []
        for ws in raw_segments:
            wstart = float(ws.get("start", 0))
            wend = float(ws.get("end", 0))
            if wend <= s or wstart >= e:
                continue
            text = (ws.get("text") or "").strip()
            if text:
                parts.append(text)
        block_text = " ".join(parts)
        out.append(
            f"=== Блок {i} ===\n"
            f"Заголовок: {b.get('title', '')}\n"
            f"Время: {sm}:{ss:02d}–{em}:{es:02d}\n"
            f"Резюме: {b.get('summary', '')}\n"
            f"Текст:\n{block_text}\n"
        )
    return "\n".join(out)


def call_claude(input_path: Path, output_path: Path, *, timeout: int = 1200) -> None:
    prompt = PROMPT_TEMPLATE.format(input_path=input_path, output_path=output_path)
    result = subprocess.run(
        ["claude", "--print", "--allowedTools=Read,Write"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude exited {result.returncode}\n"
            f"STDOUT: {result.stdout[-500:]}\n"
            f"STDERR: {result.stderr[-500:]}"
        )
    tail = (result.stdout.strip().splitlines() or [""])[-1]
    if "FAIL" in tail.upper():
        raise RuntimeError(f"claude reported failure: {tail}")


VALID_TYPES = frozenset(
    {"term", "technique", "example", "claim", "recommendation",
     "exercise", "warning", "question", "metaphor"}
)


def validate_concepts(concepts_path: Path) -> list[dict]:
    text = concepts_path.read_text(encoding="utf-8").strip()
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"ожидался JSON-массив, получил {type(data).__name__}")
    for i, c in enumerate(data):
        for key in ("name", "type", "description", "source_block_indices"):
            if key not in c:
                raise ValueError(f"концепт {i}: нет поля {key}")
        if c["type"] not in VALID_TYPES:
            raise ValueError(f"концепт {i}: тип {c['type']!r} не в таксономии")
        if not isinstance(c["source_block_indices"], list) or not all(
            isinstance(x, int) for x in c["source_block_indices"]
        ):
            raise ValueError(f"концепт {i}: source_block_indices должен быть list[int]")
    return data


def process(raw_json_path: Path) -> tuple[str, str]:
    seg_path = raw_json_path.parent / "segments.json"
    if not seg_path.exists():
        return ("error", f"нет {seg_path}, сначала запусти segment_via_claude.py")
    out = raw_json_path.parent / "concepts.json"
    if out.exists():
        return ("skipped", str(out))

    raw = json.loads(raw_json_path.read_text(encoding="utf-8"))
    segments = json.loads(seg_path.read_text(encoding="utf-8"))
    blocks_text = render_blocks(raw, segments)

    tmp = raw_json_path.parent / "_for_claude_concepts.txt"
    tmp.write_text(blocks_text, encoding="utf-8")
    try:
        call_claude(tmp, out)
        concepts = validate_concepts(out)
    finally:
        tmp.unlink(missing_ok=True)
    return ("ok", f"{len(concepts)} концептов → {out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_json", nargs="?", type=Path)
    args = parser.parse_args()

    targets = (
        [args.raw_json] if args.raw_json
        else sorted(Path("data/transcripts").glob("*/raw.json"))
    )
    if not targets:
        print("Не нашёл raw.json", file=sys.stderr)
        return 1

    for raw in targets:
        name = raw.parent.name
        try:
            status, info = process(raw)
            marker = "[+]" if status == "ok" else ("[=]" if status == "skipped" else "[!]")
            stream = sys.stderr if status == "error" else sys.stdout
            print(f"  {marker} {name}: {info}", file=stream)
        except Exception as e:
            print(f"  [!] {name}: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
