"""CLI: транскрибировать один аудиофайл.

Запуск:
    docker compose run --rm app python scripts/transcribe.py data/lectures/file.m4a
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from psy_helper.pipelines.transcribe import TranscribeConfig, transcribe


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Transcribe audio with WhisperX")
    parser.add_argument("audio", type=Path, help="Путь к аудиофайлу")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Папка для raw.json/metadata.json (по умолчанию data/transcripts/<stem>)",
    )
    parser.add_argument("--language", default="ru")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument(
        "--no-diarize",
        action="store_true",
        help="Отключить диаризацию (не требует HF_TOKEN)",
    )
    args = parser.parse_args()

    if not args.audio.exists():
        print(f"Файл не найден: {args.audio}", file=sys.stderr)
        return 1

    output = args.output or Path("data/transcripts") / args.audio.stem
    hf_token = None if args.no_diarize else os.getenv("HF_TOKEN") or None

    if not args.no_diarize and not hf_token:
        print(
            "HF_TOKEN не задан. Запусти с --no-diarize или добавь токен в .env "
            "(см. .env.example).",
            file=sys.stderr,
        )
        return 2

    config = TranscribeConfig(
        model_name=args.model, language=args.language, device=args.device
    )
    transcribe(args.audio, output, hf_token=hf_token, config=config)
    print(f"Готово. Вывод: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
