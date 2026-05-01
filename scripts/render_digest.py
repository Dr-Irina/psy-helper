"""Сводки в markdown для удобного чтения глазами.

Создаёт:
  - data/concepts_digest.md — все концепты по типам (с источниками)
  - data/transcripts/<lecture>/digest.md — сегменты лекции + связанные концепты

Запуск (на хосте, stdlib only):
    python3 scripts/render_digest.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


TYPE_LABELS = {
    "term": "Термины",
    "technique": "Техники",
    "example": "Примеры",
    "claim": "Утверждения и принципы",
    "recommendation": "Рекомендации",
    "exercise": "Упражнения",
    "warning": "Предостережения",
    "question": "Вопросы для рефлексии",
    "metaphor": "Метафоры",
}


def fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def load_lecture(transcript_dir: Path) -> dict | None:
    seg_path = transcript_dir / "segments.json"
    con_path = transcript_dir / "concepts.json"
    if not seg_path.exists() or not con_path.exists():
        return None
    return {
        "name": transcript_dir.name,
        "dir": transcript_dir,
        "segments": json.loads(seg_path.read_text(encoding="utf-8")),
        "concepts": json.loads(con_path.read_text(encoding="utf-8")),
    }


def render_lecture_digest(lecture: dict) -> str:
    out = [f"# {lecture['name']}\n\n"]
    out.append(
        f"**{len(lecture['segments'])}** смысловых блоков, "
        f"**{len(lecture['concepts'])}** концептов.\n\n"
    )

    # Сегменты с прикреплёнными к ним концептами
    concepts_by_block: dict[int, list[dict]] = defaultdict(list)
    for c in lecture["concepts"]:
        for idx in c.get("source_block_indices", []):
            concepts_by_block[int(idx)].append(c)

    out.append("## Содержание\n\n")
    for i, b in enumerate(lecture["segments"], 1):
        out.append(f"- [Блок {i}](#блок-{i}-{b['title'].lower().replace(' ', '-')[:40]}) — {b['title']}\n")
    out.append("\n---\n\n")

    for i, b in enumerate(lecture["segments"], 1):
        s, e = b["start_ts"], b["end_ts"]
        out.append(
            f"## Блок {i}: {b['title']}\n\n"
            f"_{fmt_ts(s)}–{fmt_ts(e)} • {b.get('summary', '').strip()}_\n\n"
        )
        if i in concepts_by_block:
            out.append("**Концепты:**\n\n")
            for c in concepts_by_block[i]:
                ctype = c.get("type", "?")
                label = TYPE_LABELS.get(ctype, ctype)
                desc = (c.get("description") or "").strip()
                out.append(f"- _{label}:_ **{c['name']}** — {desc}\n")
            out.append("\n")
    return "".join(out)


def render_global_concepts_digest(lectures: list[dict]) -> str:
    # Дедупликация концептов по name (если в разных лекциях встречается)
    by_type: dict[str, dict[str, dict]] = defaultdict(dict)
    for lec in lectures:
        for c in lec["concepts"]:
            ctype = c.get("type", "?")
            name = c.get("name", "")
            existing = by_type[ctype].get(name)
            if existing:
                existing.setdefault("source_lectures", []).append(lec["name"])
            else:
                c2 = dict(c)
                c2["source_lectures"] = [lec["name"]]
                by_type[ctype][name] = c2

    out = ["# База знаний — концепты по типам\n\n"]
    total = sum(len(v) for v in by_type.values())
    out.append(f"Всего уникальных концептов: **{total}** в **{len(lectures)}** лекциях.\n\n")

    out.append("## Сводка\n\n| Тип | Кол-во |\n|---|---|\n")
    for ctype, label in TYPE_LABELS.items():
        out.append(f"| {label} | {len(by_type.get(ctype, {}))} |\n")
    out.append("\n---\n\n")

    for ctype, label in TYPE_LABELS.items():
        items = by_type.get(ctype, {})
        if not items:
            continue
        out.append(f"## {label} ({len(items)})\n\n")
        for name in sorted(items):
            c = items[name]
            desc = (c.get("description") or "").strip()
            sources = ", ".join(sorted(set(c["source_lectures"])))
            out.append(f"### {name}\n\n{desc}\n\n_Источник: {sources}_\n\n")
        out.append("---\n\n")
    return "".join(out)


def main() -> int:
    transcripts = sorted(Path("data/transcripts").iterdir())
    lectures = []
    for d in transcripts:
        if not d.is_dir():
            continue
        lec = load_lecture(d)
        if lec:
            lectures.append(lec)

    if not lectures:
        print("Не нашёл лекций с segments.json и concepts.json", file=sys.stderr)
        return 1

    for lec in lectures:
        out_path = lec["dir"] / "digest.md"
        out_path.write_text(render_lecture_digest(lec), encoding="utf-8")
        print(f"  {lec['name']}: {out_path}")

    global_path = Path("data/concepts_digest.md")
    global_path.write_text(render_global_concepts_digest(lectures), encoding="utf-8")
    print(f"\n  глобальный дайджест: {global_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
