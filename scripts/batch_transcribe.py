"""Батч-обработка всех аудио в data/lectures/.

Идемпотентно: пропускает файлы, для которых уже есть data/transcripts/<stem>/raw.json.
Модели загружаются один раз и переиспользуются между файлами.

Запуск:
    docker compose run -d --name psy-batch app python scripts/batch_transcribe.py
    docker logs -f psy-batch
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from psy_helper.pipelines.transcribe import (
    TranscribeConfig,
    load_models,
    transcribe_one,
)

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".mp4"}


def find_audio_files(lectures_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in lectures_dir.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}"


def main() -> int:
    load_dotenv()
    hf_token = os.getenv("HF_TOKEN") or None

    lectures_dir = Path("data/lectures")
    transcripts_dir = Path("data/transcripts")

    if not lectures_dir.exists():
        print(f"Папка не найдена: {lectures_dir}", file=sys.stderr)
        return 1

    files = find_audio_files(lectures_dir)
    if not files:
        print(f"В {lectures_dir} нет аудиофайлов")
        return 0

    pending = []
    skipped = []
    for f in files:
        if (transcripts_dir / f.stem / "raw.json").exists():
            skipped.append(f)
        else:
            pending.append(f)

    print(f"Всего файлов: {len(files)}")
    print(f"  уже обработано (skip): {len(skipped)}")
    print(f"  в очереди: {len(pending)}")
    if not pending:
        print("Нечего обрабатывать.")
        return 0

    print()
    print("Загрузка моделей...")
    t0 = time.monotonic()
    config = TranscribeConfig()
    models = load_models(config, hf_token=hf_token)
    print(f"Модели загружены за {fmt_duration(time.monotonic() - t0)}")
    print()

    failures: list[tuple[Path, str]] = []
    batch_start = time.monotonic()

    for i, audio in enumerate(pending, 1):
        out = transcripts_dir / audio.stem
        print(f"[{i}/{len(pending)}] {audio.name}")
        t = time.monotonic()
        try:
            transcribe_one(audio, out, models, config)
            elapsed = time.monotonic() - t
            print(f"  OK за {fmt_duration(elapsed)} → {out}")
        except Exception as e:
            elapsed = time.monotonic() - t
            err = f"{type(e).__name__}: {e}"
            failures.append((audio, err))
            print(f"  FAIL за {fmt_duration(elapsed)}: {err}", file=sys.stderr)

    total = time.monotonic() - batch_start
    done = len(pending) - len(failures)
    print()
    print(f"Готово. Обработано: {done}/{len(pending)} за {fmt_duration(total)}")
    if failures:
        print(f"Ошибки ({len(failures)}):")
        for f, err in failures:
            print(f"  {f.name}: {err}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
