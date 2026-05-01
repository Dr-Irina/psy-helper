"""Рендер raw.json в читабельный transcript.md.

По умолчанию обходит все data/transcripts/*/raw.json и пишет рядом transcript.md.
Соседние сегменты одного спикера склеиваются в блок.

Запуск (локально на Mac, без docker — только stdlib):
    python3 scripts/render_markdown.py
    python3 scripts/render_markdown.py path/to/raw.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render(raw_json_path: Path, output_path: Path) -> dict:
    data = json.loads(raw_json_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])

    out: list[str] = []
    title = raw_json_path.parent.name
    out.append(f"# {title}\n\n")
    out.append(f"_Сегментов: {len(segments)}_\n\n")
    out.append("---\n\n")

    speakers_seen: set[str] = set()
    current_speaker: str | None = None
    block_start: float = 0.0
    block_lines: list[str] = []

    def flush() -> None:
        if not block_lines:
            return
        sp = current_speaker or "?"
        out.append(f"**[{fmt_ts(block_start)}] {sp}:**\n\n")
        out.append(" ".join(block_lines) + "\n\n")

    for seg in segments:
        sp = seg.get("speaker") or "?"
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speakers_seen.add(sp)
        if sp != current_speaker:
            flush()
            current_speaker = sp
            block_start = float(seg.get("start", 0.0))
            block_lines = []
        block_lines.append(text)
    flush()

    output_path.write_text("".join(out), encoding="utf-8")
    return {
        "segments": len(segments),
        "speakers": sorted(speakers_seen),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "raw_json",
        nargs="?",
        type=Path,
        help="Путь к raw.json. Если не задан — обходит data/transcripts/*/raw.json",
    )
    args = parser.parse_args()

    if args.raw_json:
        targets = [args.raw_json]
    else:
        targets = sorted(Path("data/transcripts").glob("*/raw.json"))

    if not targets:
        print("Не нашёл raw.json", file=sys.stderr)
        return 1

    for raw in targets:
        out = raw.parent / "transcript.md"
        info = render(raw, out)
        print(f"  {raw.parent.name}: {info['segments']} сегм., {len(info['speakers'])} спикеров → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
