"""Сегментация лекций по смыслу через claude -p (без Anthropic API).

Для каждой data/transcripts/<lecture>/raw.json — рендерит транскрипт в текст,
вызывает claude --print с промптом-инструкцией, получает segments.json
рядом с raw.json. Идемпотентно: если segments.json уже есть — пропускает.

Запуск (на хосте, не в Docker — нужен бинарь claude):
    python3 scripts/segment_via_claude.py
    python3 scripts/segment_via_claude.py "data/transcripts/<имя>/raw.json"
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


PROMPT_TEMPLATE = """Ты обрабатываешь транскрипт лекции психолога Анны для базы знаний.

Задача: разрезать транскрипт на смысловые блоки. Каждый блок — связная подтема.

Правила:
- Режь по смыслу, не по таймингу. Хороший блок — обычно 2–15 минут.
- Граница блока — там, где меняется тема обсуждения, ключевая идея, фокус.
- Краткое введение/приветствие — отдельный блок ("Введение и знакомство").
- Финальное резюме/вопросы — отдельный блок ("Завершение").
- Покрой ВЕСЬ транскрипт без пропусков: end_ts блока = start_ts следующего.
- Каждому блоку: короткий title (3–7 слов на русском) и summary (1–2 предложения).

Сделай ровно следующее:
1. Прочитай файл {input_path}
2. Разрежь содержимое на смысловые блоки
3. Запиши результат в файл {output_path} как валидный JSON-массив:
[
  {{"title": "...", "summary": "...", "start_ts": 0.0, "end_ts": 240.5}},
  ...
]

После записи файла напиши единственное слово OK и ничего больше.
Если что-то пошло не так — напиши FAIL: <причина>.
"""


def render_for_claude(raw_data: dict) -> str:
    lines = []
    for seg in raw_data.get("segments", []):
        start = float(seg.get("start", 0))
        m, s = divmod(int(start), 60)
        sp = seg.get("speaker") or "?"
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"[{m}:{s:02d}] {sp}: {text}")
    return "\n".join(lines)


def call_claude(input_path: Path, output_path: Path, *, timeout: int = 600) -> None:
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
    tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if "FAIL" in tail.upper():
        raise RuntimeError(f"claude reported failure: {tail}")


def validate_segments(segments_path: Path) -> list[dict]:
    text = segments_path.read_text(encoding="utf-8").strip()
    # На всякий случай выдрать JSON из markdown-обёртки, если вдруг
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"ожидался JSON-массив, получил {type(data).__name__}")
    for i, seg in enumerate(data):
        for key in ("title", "summary", "start_ts", "end_ts"):
            if key not in seg:
                raise ValueError(f"сегмент {i}: нет поля {key}")
    return data


def process(raw_json_path: Path) -> tuple[str, str]:
    out = raw_json_path.parent / "segments.json"
    if out.exists():
        return ("skipped", str(out))

    raw = json.loads(raw_json_path.read_text(encoding="utf-8"))
    text = render_for_claude(raw)
    tmp = raw_json_path.parent / "_for_claude.txt"
    tmp.write_text(text, encoding="utf-8")
    try:
        call_claude(tmp, out)
        segs = validate_segments(out)
    finally:
        tmp.unlink(missing_ok=True)
    return ("ok", f"{len(segs)} blocks → {out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_json", nargs="?", type=Path, help="Путь к raw.json")
    args = parser.parse_args()

    targets = [args.raw_json] if args.raw_json else sorted(
        Path("data/transcripts").glob("*/raw.json")
    )
    if not targets:
        print("Не нашёл raw.json", file=sys.stderr)
        return 1

    for raw in targets:
        name = raw.parent.name
        try:
            status, info = process(raw)
            marker = "[+]" if status == "ok" else "[=]"
            print(f"  {marker} {name}: {info}")
        except Exception as e:
            print(f"  [!] {name}: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
