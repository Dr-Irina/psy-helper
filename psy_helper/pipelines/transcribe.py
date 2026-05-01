"""WhisperX транскрипция с pyannote-диаризацией. ТЗ раздел 4.1."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# PyTorch 2.6+ переключил torch.load на weights_only=True по умолчанию.
# Чекпоинты pyannote содержат omegaconf и lightning-объекты, не входящие
# в safe-globals, и валятся с UnpicklingError. Модели тянем из официального
# huggingface-репо pyannote — источник доверенный, weights_only=False безопасен.
import torch as _torch

_orig_torch_load = _torch.load

def _patched_torch_load(*args, **kwargs):
    # Принудительно — lightning_fabric и pyannote передают weights_only=True
    # явно, и setdefault не помогает.
    kwargs["weights_only"] = False
    return _orig_torch_load(*args, **kwargs)

_torch.load = _patched_torch_load

import whisperx
from whisperx.diarize import DiarizationPipeline


@dataclass
class TranscribeConfig:
    model_name: str = "large-v3"
    language: str = "ru"
    device: str = field(default_factory=lambda: os.getenv("WHISPER_DEVICE", "cpu"))
    compute_type: str = field(
        default_factory=lambda: os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    )
    batch_size: int = field(
        default_factory=lambda: int(os.getenv("WHISPER_BATCH_SIZE", "8"))
    )
    diarization_model: str = "pyannote/speaker-diarization-3.1"


@dataclass
class LoadedModels:
    whisper: Any
    align_model: Any
    align_metadata: Any
    diarize: DiarizationPipeline | None


def load_models(config: TranscribeConfig, hf_token: str | None = None) -> LoadedModels:
    whisper = whisperx.load_model(
        config.model_name,
        config.device,
        compute_type=config.compute_type,
        language=config.language,
    )
    align_model, align_metadata = whisperx.load_align_model(
        language_code=config.language, device=config.device
    )
    diarize = None
    if hf_token:
        diarize = DiarizationPipeline(
            model_name=config.diarization_model,
            token=hf_token,
            device=config.device,
        )
    return LoadedModels(whisper, align_model, align_metadata, diarize)


def transcribe_one(
    audio_path: Path,
    output_dir: Path,
    models: LoadedModels,
    config: TranscribeConfig,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    audio = whisperx.load_audio(str(audio_path))
    result = models.whisper.transcribe(
        audio, batch_size=config.batch_size, language=config.language
    )
    result = whisperx.align(
        result["segments"],
        models.align_model,
        models.align_metadata,
        audio,
        config.device,
        return_char_alignments=False,
    )

    raw_path = output_dir / "raw.json"
    raw_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    diarized = False
    diarize_error: str | None = None
    if models.diarize is not None:
        try:
            diarize_segments = models.diarize(audio)
            result = whisperx.assign_word_speakers(diarize_segments, result)
            raw_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            diarized = True
        except Exception as e:
            diarize_error = f"{type(e).__name__}: {e}"

    metadata = {
        "audio_path": str(audio_path),
        "model": config.model_name,
        "language": config.language,
        "device": config.device,
        "compute_type": config.compute_type,
        "diarized": diarized,
        "diarize_error": diarize_error,
        "segment_count": len(result.get("segments", [])),
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return result


def transcribe(
    audio_path: Path,
    output_dir: Path,
    *,
    hf_token: str | None,
    config: TranscribeConfig | None = None,
) -> dict:
    cfg = config or TranscribeConfig()
    models = load_models(cfg, hf_token)
    return transcribe_one(audio_path, output_dir, models, cfg)
