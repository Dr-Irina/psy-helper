"""Генерация черновика voice-document Анны через claude -p.

Собирает материал из всех лекций:
- Концепты по 9 типам (из concepts.json) — основа для принципов и стиля
- Длинные прямые цитаты Анны (из raw.json, фильтр по доминантному спикеру)

Передаёт это Claude с инструкцией написать структурированный markdown
по разделам: принципы / red lines / стиль / фирменные формулировки /
техники / рекомендации / что нужно дополнить из интервью.

Запуск (на хосте):
    python3 scripts/generate_voice_doc_via_claude.py
Выход: data/voice_document/v1_draft.md
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


PROMPT_TEMPLATE = """Ты помогаешь составить voice-document психолога Анны.

voice-document — это структурированное описание того, как Анна работает:
её принципы, чего она НЕ делает, как она говорит, фирменные формулировки.
Это используется и как референс для команды, и в дальнейшем — для бота,
отвечающего клиентам в её стиле.

ВАЖНО: это ЧЕРНОВИК v1. Где не уверен — пиши `[нужно подтвердить с Анной]`.

Тебе дан файл {input_path}, в нём:
1. Концепты из её 2 лекций, сгруппированные по 9 типам
2. Прямые цитаты Анны (длинные куски её речи)

Сделай ровно следующее:
1. Прочитай {input_path}
2. Запиши в {output_path} markdown ровно такой структуры (разделы строго в этом порядке):

```
# Voice-document Анны (v1, черновик)

> Этот черновик сгенерирован автоматически из её лекций. Анна должна его прочитать и поправить.

## 1. Принципы работы

5–10 ключевых установок, которыми Анна руководствуется. Брать из claim-концептов и её прямой речи. Каждый пункт — 1–2 предложения, конкретно, БЕЗ абстракции. Формулировка от первого лица: «Я считаю, что…», «Я работаю исходя из того, что…».

## 2. Red lines (чего я НЕ делаю)

5–10 явных запретов из её warning-концептов и принципов. От первого лица: «Я не…», «Я никогда не…».

## 3. Стилевая характеристика

Как Анна говорит: регистр (живой/академичный/смешанный), длина фраз, обращение к клиенту, юмор, ругательства/просторечия, профессиональные термины. Делать выводы из её прямых цитат.

## 4. Фирменные формулировки

5–10 узнаваемых фраз, вопросов, метафор. Из question и metaphor концептов + из прямой речи. По формату: «Цитата» — что это значит / когда применяется.

## 5. Подходы и техники

Краткий обзор её инструментария: 5–10 техник из метода. По 1 предложению о каждой. Это «что у меня в наборе», не пошаговое описание.

## 6. Рекомендуемые источники

Книги, авторы, ресурсы, которые она упоминает. Из recommendation-концептов.

## 7. Что нужно дополнить из интервью с Анной

Список из 5–10 пунктов, которых нет в лекциях, но они нужны для полного voice-doc. То, что Анна должна явно сказать команде на интервью. Например:
- Точное описание границ метода (когда отказываешь в работе)
- Что делаешь, если клиент в кризисе
- Как описываешь терапевтический альянс клиенту на первой встрече
- ... и т.д.
```

После записи файла напиши OK и ничего больше. Если ошибка — FAIL: <причина>.
"""


def dominant_speaker(segments: list[dict]) -> str | None:
    durations: dict[str, float] = {}
    for s in segments:
        sp = s.get("speaker")
        if not sp:
            continue
        durations[sp] = durations.get(sp, 0.0) + float(s.get("end", 0)) - float(s.get("start", 0))
    if not durations:
        return None
    return max(durations.items(), key=lambda kv: kv[1])[0]


def speech_samples(raw_path: Path, max_chars: int = 7000, max_blocks: int = 20) -> list[str]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    segs = raw.get("segments", [])
    dom = dominant_speaker(segs)
    if not dom:
        return []
    blocks: list[str] = []
    current: list[str] = []
    for s in segs:
        if s.get("speaker") == dom:
            text = (s.get("text") or "").strip()
            if text:
                current.append(text)
        else:
            if current:
                blocks.append(" ".join(current))
                current = []
    if current:
        blocks.append(" ".join(current))
    blocks.sort(key=len, reverse=True)
    out: list[str] = []
    total = 0
    for b in blocks:
        if total + len(b) > max_chars:
            continue
        out.append(b)
        total += len(b)
        if len(out) >= max_blocks:
            break
    return out


def collect_concepts() -> dict[str, list[dict]]:
    by_type: dict[str, dict[str, dict]] = defaultdict(dict)
    for path in sorted(Path("data/transcripts").glob("*/concepts.json")):
        for c in json.loads(path.read_text(encoding="utf-8")):
            ctype = c.get("type", "?")
            name = c.get("name", "")
            existing = by_type[ctype].get(name)
            if existing:
                continue
            by_type[ctype][name] = c
    return {k: list(v.values()) for k, v in by_type.items()}


def build_input(concepts_by_type: dict[str, list[dict]], samples: list[str]) -> str:
    type_labels = {
        "term": "Термины метода",
        "technique": "Техники / приёмы",
        "claim": "Утверждения и принципы",
        "warning": "Предостережения (red lines)",
        "recommendation": "Рекомендации (книги, ресурсы)",
        "exercise": "Упражнения",
        "question": "Вопросы для рефлексии",
        "metaphor": "Метафоры",
        "example": "Примеры / кейсы",
    }
    out = ["# Материал для voice-document\n\n"]
    out.append("## Концепты, извлечённые из лекций (по 9 типам)\n\n")
    for ctype, label in type_labels.items():
        items = concepts_by_type.get(ctype, [])
        if not items:
            continue
        out.append(f"### {label} ({len(items)})\n\n")
        for c in items:
            desc = (c.get("description") or "").strip()
            out.append(f"- **{c.get('name', '')}** — {desc}\n")
        out.append("\n")
    out.append(f"## Прямые цитаты Анны (топ-{len(samples)} по длине)\n\n")
    for i, s in enumerate(samples, 1):
        out.append(f"### Цитата {i}\n\n> {s}\n\n")
    return "".join(out)


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


def main() -> int:
    out_dir = Path("data/voice_document")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "v1_draft.md"
    if out_path.exists():
        print(f"Уже существует: {out_path}. Удали его, если хочешь перегенерить.")
        return 0

    concepts_by_type = collect_concepts()
    n_concepts = sum(len(v) for v in concepts_by_type.values())
    print(f"Уникальных концептов: {n_concepts}")

    all_samples: list[str] = []
    for raw_path in sorted(Path("data/transcripts").glob("*/raw.json")):
        all_samples.extend(speech_samples(raw_path))
    # Топ-15 после объединения, по длине
    all_samples.sort(key=len, reverse=True)
    all_samples = all_samples[:15]
    print(f"Цитат Анны (длинных кусков прямой речи): {len(all_samples)}")

    input_text = build_input(concepts_by_type, all_samples)
    tmp = out_dir / "_input_for_claude.md"
    tmp.write_text(input_text, encoding="utf-8")
    print(f"Размер материала для Claude: {len(input_text)} символов")

    try:
        print("Вызываю claude -p…")
        call_claude(tmp, out_path)
        print(f"Готово: {out_path}")
    finally:
        tmp.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
