"""Сводка для встречи с Анной: что распарсилось из 2 лекций.

Формат: один markdown с чекбоксами по каждому пункту. Анна и Ира на встрече
ставят галочки/правки прямо в файле или на распечатке.

Запуск:
    python3 scripts/render_review.py
Выход: data/review_for_meeting.md
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


TYPE_LABELS_ORDERED = [
    ("term", "Термины метода"),
    ("technique", "Техники / приёмы"),
    ("claim", "Утверждения и принципы"),
    ("warning", "Предостережения"),
    ("recommendation", "Рекомендации (книги, фильмы, ресурсы)"),
    ("exercise", "Упражнения / домашки"),
    ("question", "Вопросы для рефлексии"),
    ("metaphor", "Метафоры и образы"),
    ("example", "Примеры / кейсы"),
]


def fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def render_lecture(lecture_dir: Path) -> str:
    name = lecture_dir.name
    segs = json.loads((lecture_dir / "segments.json").read_text(encoding="utf-8"))
    cons = json.loads((lecture_dir / "concepts.json").read_text(encoding="utf-8"))

    out = [f"## Лекция: {name}\n\n"]
    out.append(
        f"_{len(segs)} смысловых блоков, {len(cons)} концептов._\n\n"
    )

    out.append("### 1. Сегментация — карта блоков\n\n")
    out.append("_Проверьте: верно ли нарезано? Где блок надо объединить или разрезать?_\n\n")
    for i, b in enumerate(segs, 1):
        s, e = b["start_ts"], b["end_ts"]
        summary = (b.get("summary") or "").strip()
        out.append(
            f"- [ ] **{i}. {b['title']}** _[{fmt_ts(s)}–{fmt_ts(e)}]_  \n"
            f"  {summary}  \n"
            f"  _Комментарий:_\n\n"
        )

    out.append("### 2. Концепты по типам\n\n")
    out.append(
        "_Проверьте: тип верный? Имя точное? Описание правильно отражает мысль? Что-то лишнее, что выкинуть?_\n\n"
    )
    by_type: dict[str, list[dict]] = defaultdict(list)
    for c in cons:
        by_type[c.get("type", "?")].append(c)

    for ctype, label in TYPE_LABELS_ORDERED:
        items = by_type.get(ctype, [])
        if not items:
            continue
        out.append(f"#### {label} ({len(items)})\n\n")
        for c in items:
            name_c = c.get("name", "")
            desc = (c.get("description") or "").strip()
            blocks = ",".join(map(str, c.get("source_block_indices", [])))
            out.append(
                f"- [ ] **{name_c}** _(блоки {blocks})_  \n"
                f"  {desc}  \n"
                f"  _Комментарий:_\n\n"
            )
        out.append("\n")
    out.append("---\n\n")
    return "".join(out)


def main() -> int:
    lectures = sorted(
        d for d in Path("data/transcripts").iterdir()
        if d.is_dir()
        and (d / "segments.json").exists()
        and (d / "concepts.json").exists()
    )
    if not lectures:
        print("Не нашёл пар segments.json + concepts.json", file=sys.stderr)
        return 1

    out = [
        "# Ревью разметки лекций — встреча с Анной\n\n",
        "**Цель встречи:** глазами просмотреть всё, что распарсилось из лекций. ",
        "Зафиксировать что верно, что неверно, что переименовать, что удалить.\n\n",
        "**Как пользоваться:**  \n",
        "- Пройти по чекбоксам сверху вниз  \n",
        "- ✓ — всё верно  \n",
        "- Если что-то не так — оставить комментарий в строке `_Комментарий:_`  \n",
        "- Можно прямо в файле, можно на распечатке  \n\n",
        "**Источники данных:** транскрипция WhisperX → смысловая сегментация Claude → извлечение концептов Claude.\n\n",
        "---\n\n",
    ]
    for lec in lectures:
        out.append(render_lecture(lec))

    out_path = Path("data/review_for_meeting.md")
    out_path.write_text("".join(out), encoding="utf-8")
    print(f"Готово: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
