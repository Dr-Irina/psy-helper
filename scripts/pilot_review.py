"""Пилот: сравнить старые концепты (concepts.json) и новые (concepts_v2.json).

Автоскоринг (объективное) + side-by-side для ручной оценки голоса/значимости.
Цель — выбрать модель/промпт для перевыделения (см. план).

Авто-метрики по новому корпусу:
  - сколько концептов, сколько с цитатами, среднее число цитат
  - распределение salience (1/2/3)
  - распределение типов
  - доля концептов, где есть хоть одна валидная цитата (после фильтра уже все, но
    отчёт показывает объём)

Ручная оценка (в data/pilot_review.md): по каждому новому концепту видно name,
description, цитаты Ани, salience — глазами проверяешь голос (живая речь, не пересказ)
и адекватность веса. Рядом — старые концепты той же лекции для контраста.

Запуск:
    python3 scripts/pilot_review.py "Кофе с психологом. Тревожность" "..."
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

TRANSCRIPTS = Path("data/transcripts")
OUT = Path("data/pilot_review.md")


def load(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("lectures", nargs="*", help="имена папок (по умолчанию все с concepts_v2.json)")
    args = ap.parse_args()

    if args.lectures:
        dirs = [TRANSCRIPTS / n for n in args.lectures]
    else:
        dirs = sorted(p.parent for p in TRANSCRIPTS.glob("*/concepts_v2.json"))

    md = ["# Пилот перевыделения: old vs new\n"]
    g_types, g_sal = Counter(), Counter()
    g_n_new = g_n_quotes = g_quotes = g_n_old = 0

    for d in dirs:
        new = load(d / "concepts_v2.json")
        old = load(d / "concepts.json")
        if new is None:
            continue
        md.append(f"\n## {d.name}\n")
        n_q = sum(1 for c in new if c.get("quotes"))
        n_quotes = sum(len(c.get("quotes") or []) for c in new)
        sal = Counter(c.get("salience") for c in new)
        typ = Counter(c.get("type") for c in new)
        g_n_new += len(new); g_n_quotes += n_q; g_quotes += n_quotes
        g_n_old += len(old or []); g_sal += sal; g_types += typ
        md.append(f"**Новых: {len(new)}** (с цитатами: {n_q}, цитат всего: {n_quotes}) | "
                  f"старых: {len(old or [])} | salience 3/2/1: {sal[3]}/{sal[2]}/{sal[1]}\n")

        md.append("\n### НОВЫЕ концепты (проверь голос и вес)\n")
        for c in sorted(new, key=lambda x: -(x.get("salience") or 0)):
            md.append(f"- **[{c.get('salience')}·{c.get('type')}] {c.get('name')}**")
            md.append(f"  - _смысл (поиск):_ {c.get('description','')}")
            for q in (c.get("quotes") or []):
                md.append(f"  - 🗣 «{q}»")
        if old:
            md.append("\n### СТАРЫЕ концепты (для контраста — пересказ)\n")
            for c in old:
                md.append(f"- [{c.get('type')}] {c.get('name')} — {c.get('description','')}")

    md.insert(1, (
        f"\n**ИТОГО по пилоту:** новых концептов {g_n_new} (с цитатами {g_n_quotes}, "
        f"цитат {g_quotes}), старых {g_n_old}.\n"
        f"Типы: {dict(g_types)}\nSalience 3/2/1: {g_sal[3]}/{g_sal[2]}/{g_sal[1]}\n"
        f"Среднее цитат на концепт: {g_quotes/g_n_new:.1f}\n" if g_n_new else "\n(нет new-концептов)\n"
    ))

    OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Отчёт: {OUT}")
    print(f"Новых концептов: {g_n_new} | цитат: {g_quotes} | типы: {dict(g_types)} | "
          f"salience 3/2/1: {g_sal[3]}/{g_sal[2]}/{g_sal[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
