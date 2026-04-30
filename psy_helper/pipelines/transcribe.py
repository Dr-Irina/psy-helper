"""WhisperX транскрипция с pyannote-диаризацией. ТЗ раздел 4.1."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import whisperx


@dataclass
class TranscribeConfig:
    model_name: str = "large-v3"
    language: str = "ru"
    device: str = "cpu"
    compute_type: str = "int8"
    batch_size: int = 8


def transcribe(
    audio_path: Path,
    output_dir: Path,
    *,
    hf_token: str | None,
    config: TranscribeConfig | None = None,
) -> dict:
    cfg = config or TranscribeConfig()
    output_dir.mkdir(parents=True, exist_ok=True)

    model = whisperx.load_model(
        cfg.model_name,
        cfg.device,
        compute_type=cfg.compute_type,
        language=cfg.language,
    )
    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio, batch_size=cfg.batch_size, language=cfg.language)

    align_model, align_metadata = whisperx.load_align_model(
        language_code=cfg.language, device=cfg.device
    )
    result = whisperx.align(
        result["segments"],
        align_model,
        align_metadata,
        audio,
        cfg.device,
        return_char_alignments=False,
    )

    diarized = False
    if hf_token:
        diarize_model = whisperx.DiarizationPipeline(
            use_auth_token=hf_token, device=cfg.device
        )
        diarize_segments = diarize_model(audio)
        result = whisperx.assign_word_speakers(diarize_segments, result)
        diarized = True

    raw_path = output_dir / "raw.json"
    raw_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    metadata = {
        "audio_path": str(audio_path),
        "model": cfg.model_name,
        "language": cfg.language,
        "device": cfg.device,
        "compute_type": cfg.compute_type,
        "diarized": diarized,
        "segment_count": len(result.get("segments", [])),
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return result
