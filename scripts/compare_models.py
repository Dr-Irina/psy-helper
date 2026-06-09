"""Сравнить две модели извлечения на одних лекциях (для выбора в пилоте).

Читает concepts_v2_<a>.json и concepts_v2_<b>.json по каждой лекции, считает
объективные метрики и пишет side-by-side в data/model_compare.md для оценки голоса.

Метрики на модель:
  - концептов, цитат, среднее цитат/концепт
  - распределение salience и типов
  - английские вкрапления в description (объективный признак code-switch)
  - средняя длина description

Запуск:
    python3 scripts/compare_models.py gemma qwen "Кофе с психологом. Тревожность" "..."
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

TRANSCRIPTS = Path("data/transcripts")
OUT = Path("data/model_compare.md")
LATIN = re.compile(r"[A-Za-z]{3,}")


def load(d: Path, tag: str):
    p = d / f"concepts_v2_{tag}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def metrics(concepts):
    n = len(concepts)
    quotes = sum(len(c.get("quotes") or []) for c in concepts)
    sal = Counter(c.get("salience") for c in concepts)
    typ = Counter(c.get("type") for c in concepts)
    eng = sum(1 for c in concepts if LATIN.search(c.get("description") or ""))
    avg_desc = sum(len(c.get("description") or "") for c in concepts) / n if n else 0
    return {
        "n": n, "quotes": quotes, "avg_q": quotes / n if n else 0,
        "sal": sal, "typ": typ, "eng": eng, "avg_desc": avg_desc,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tag_a")
    ap.add_argument("tag_b")
    ap.add_argument("lectures", nargs="+")
    args = ap.parse_args()

    md = [f"# Сравнение моделей: {args.tag_a} vs {args.tag_b}\n"]
    agg = {args.tag_a: Counter(), args.tag_b: Counter()}

    for name in args.lectures:
        d = TRANSCRIPTS / name
        a, b = load(d, args.tag_a), load(d, args.tag_b)
        if a is None or b is None:
            md.append(f"\n## {name}\n(нет данных для одной из моделей)\n")
            continue
        ma, mb = metrics(a), metrics(b)
        md.append(f"\n## {name}\n")
        md.append(f"| метрика | {args.tag_a} | {args.tag_b} |")
        md.append("|---|---|---|")
        md.append(f"| концептов | {ma['n']} | {mb['n']} |")
        md.append(f"| цитат (ср/концепт) | {ma['quotes']} ({ma['avg_q']:.1f}) | {mb['quotes']} ({mb['avg_q']:.1f}) |")
        md.append(f"| salience 3/2/1 | {ma['sal'][3]}/{ma['sal'][2]}/{ma['sal'][1]} | {mb['sal'][3]}/{mb['sal'][2]}/{mb['sal'][1]} |")
        md.append(f"| англ. вкрапления в desc | {ma['eng']} | {mb['eng']} |")
        md.append(f"| ср. длина desc | {ma['avg_desc']:.0f} | {mb['avg_desc']:.0f} |")
        for tag, m in ((args.tag_a, ma), (args.tag_b, mb)):
            for k in ("n", "quotes", "eng"):
                agg[tag][k] += m[k]

        for tag, cs in ((args.tag_a, a), (args.tag_b, b)):
            md.append(f"\n### {tag} — концепты (голос)\n")
            for c in sorted(cs, key=lambda x: -(x.get("salience") or 0))[:18]:
                q = (c.get("quotes") or [""])[0]
                md.append(f"- **[{c.get('salience')}·{c.get('type')}] {c.get('name')}** — 🗣 «{q[:130]}»")

    md.insert(1, (
        f"\n**ИТОГО:** {args.tag_a}: концептов {agg[args.tag_a]['n']}, цитат {agg[args.tag_a]['quotes']}, "
        f"англ.вкраплений {agg[args.tag_a]['eng']} | "
        f"{args.tag_b}: концептов {agg[args.tag_b]['n']}, цитат {agg[args.tag_b]['quotes']}, "
        f"англ.вкраплений {agg[args.tag_b]['eng']}\n"
    ))

    OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Отчёт: {OUT}")
    for tag in (args.tag_a, args.tag_b):
        print(f"  {tag}: концептов {agg[tag]['n']} | цитат {agg[tag]['quotes']} | англ.вкраплений {agg[tag]['eng']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
