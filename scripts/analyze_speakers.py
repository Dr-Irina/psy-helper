"""Спикер-атрибуция: кто из диаризации — Аня (лектор).

Диаризация (pyannote) проставила speaker-метки в raw.json, но они анонимны
(SPEAKER_00..N) и местами пересегментированы (до ~19 «спикеров» на лекцию из-за
коротких реплик студентов/шумов). Для извлечения дословных цитат голосом Ани нам
нужно знать, КАКОЙ label — это она.

Эвристика: в лекционном формате лектор говорит подавляющую часть времени, поэтому
**доминирующий по времени речи спикер = Аня**. Скрипт считает долю каждого спикера
и:
  - пишет data/speakers.json: { "<lecture_dir>": {"anna": "SPEAKER_X",
        "dominance": 0.83, "ambiguous": false, "n_speakers": 12} }
  - печатает читабельный отчёт; AMBIGUOUS-лекции (доминанта < порога или близкий
    второй спикер — возможен диалог / два автора Аня+Оксана) надо сверить вручную
    и при необходимости поправить anna в data/speakers.json.

Read-only по корпусу; модель не нужна. Запуск:
    python3 scripts/analyze_speakers.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

# Доля времени речи доминирующего спикера, ниже которой считаем атрибуцию спорной.
DOMINANCE_THRESHOLD = 0.60
# Если второй спикер набрал больше этого — тоже спорно (диалог / два автора).
SECOND_SPEAKER_FLAG = 0.25

TRANSCRIPTS = Path("data/transcripts")
OUT_PATH = Path("data/speakers.json")


def speaker_durations(segments: list[dict]) -> dict[str, float]:
    """Сумма длительности речи по каждому speaker-label (None пропускаем)."""
    dur: dict[str, float] = defaultdict(float)
    for s in segments:
        spk = s.get("speaker")
        if not spk:
            continue
        start = float(s.get("start", 0.0))
        end = float(s.get("end", 0.0))
        if end > start:
            dur[spk] += end - start
    return dict(dur)


def analyze_one(raw_path: Path) -> dict | None:
    try:
        data = json.loads(raw_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    segments = data.get("segments", [])
    dur = speaker_durations(segments)
    if not dur:
        return {"error": "нет speaker-меток (диаризация не прошла?)"}

    total = sum(dur.values())
    ranked = sorted(dur.items(), key=lambda kv: kv[1], reverse=True)
    top_label, top_dur = ranked[0]
    dominance = top_dur / total if total else 0.0
    second_share = (ranked[1][1] / total) if len(ranked) > 1 and total else 0.0

    ambiguous = dominance < DOMINANCE_THRESHOLD or second_share > SECOND_SPEAKER_FLAG
    return {
        "anna": top_label,
        "dominance": round(dominance, 3),
        "second": ranked[1][0] if len(ranked) > 1 else None,
        "second_share": round(second_share, 3),
        "n_speakers": len(dur),
        "ambiguous": ambiguous,
    }


def main() -> int:
    raws = sorted(TRANSCRIPTS.glob("*/raw.json"))
    if not raws:
        print("Не нашёл data/transcripts/*/raw.json")
        return 1

    result: dict[str, dict] = {}
    rows: list[tuple] = []
    for raw in raws:
        lecture = raw.parent.name
        info = analyze_one(raw)
        result[lecture] = info
        if "error" in info:
            rows.append((lecture, info["error"], "", "", ""))
            continue
        rows.append((
            lecture,
            info["anna"],
            f"{info['dominance']*100:.0f}%",
            f"{info['n_speakers']}",
            "СВЕРИТЬ" if info["ambiguous"] else "",
        ))

    OUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Отчёт
    n_amb = sum(1 for v in result.values() if v.get("ambiguous"))
    n_err = sum(1 for v in result.values() if "error" in v)
    w = max(len(r[0]) for r in rows)
    print(f"{'лекция':<{w}}  {'Аня':<11} {'дом.':>5}  {'спик.':>5}  флаг")
    print("-" * (w + 32))
    for lec, anna, dom, nspk, flag in sorted(rows, key=lambda r: r[0]):
        print(f"{lec:<{w}}  {anna:<11} {dom:>5}  {nspk:>5}  {flag}")
    print("-" * (w + 32))
    print(f"Всего: {len(rows)} | СВЕРИТЬ вручную: {n_amb} | ошибок: {n_err}")
    print(f"Карта записана в {OUT_PATH}")
    print("Поправь anna для спорных лекций прямо в этом файле, если эвристика ошиблась.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
