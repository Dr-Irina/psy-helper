"""Предложить N тем для генерации под выбранную целевую группу.

Логика:
    1. Из БД достаём top-N концептов под (topics, hunt_stages) — берём их subtopics
       и names как сырьё.
    2. Просим Haiku 4.5 сгруппировать сырьё в 10 разных тем-затравок
       (короткие фразы, годятся как --hint для generate_content.py).
    3. Печатаем нумерованный список.

Дёшево (~$0.001 за вызов на Haiku), быстро (~3 сек).

Запуск:
    docker compose run --rm app python scripts/suggest_topics.py \\
        --voice anna_product --segment tired_wife --psycho-type patient \\
        --hunt-stage 2 --topic marriage --limit 10
"""
from __future__ import annotations

import argparse
import os
import sys

from anthropic import Anthropic
from dotenv import load_dotenv

from psy_helper.content_gen.loaders import (
    list_psycho_types,
    list_segments,
    list_voice_profiles,
    load_psycho_type,
    load_segment,
    load_voice_profile,
)
from psy_helper.db.connection import connect


CONCEPT_POOL = 40   # сколько концептов вытащить из БД как сырьё
MODEL = "claude-haiku-4-5"


def fetch_concepts_pool(
    conn, topics: list[str] | None, hunt_stage: int | None, limit: int,
) -> list[dict]:
    where = []
    params = {"limit": limit}
    if topics:
        where.append("topics && %(topics)s")
        params["topics"] = topics
    if hunt_stage is not None:
        where.append("%(stage)s = ANY(hunt_stages)")
        params["stage"] = hunt_stage

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    sql = f"""
    SELECT name, type, COALESCE(description, ''), COALESCE(subtopics, ARRAY[]::text[])
    FROM concepts
    {where_sql}
    ORDER BY array_length(source_segments, 1) DESC NULLS LAST, name
    LIMIT %(limit)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [
        {"name": r[0], "type": r[1], "description": r[2][:200], "subtopics": r[3]}
        for r in rows
    ]


def build_prompt(
    pool: list[dict],
    voice_profile,
    segment,
    psycho_type,
    hunt_stage: int | None,
    limit: int,
) -> str:
    segment_block = ""
    if segment:
        pains = ", ".join(f"«{p}»" for p in segment.pain_phrases[:5])
        segment_block = (
            f"Сегмент: «{segment.name}». Их боли: {pains}\n"
            f"Главное сообщение для них: {segment.main_message.strip()}"
        )

    pt_block = ""
    if psycho_type:
        pt_block = (
            f"Психотип: «{psycho_type.name}». Цепляет: {', '.join(psycho_type.attracts[:3])}. "
            f"Отталкивает: {', '.join(psycho_type.repels[:3])}."
        )

    stage_block = f"Ступень Ханта: {hunt_stage}." if hunt_stage else ""

    pool_lines = "\n".join(
        f"- ({c['type']}) {c['name']}: {c['description']}"
        for c in pool[:30]
    )

    return (
        f"Голос автора: «{voice_profile.name}», регистр {voice_profile.register_}, "
        f"обращение «{voice_profile.form_of_address}».\n\n"
        f"{segment_block}\n{pt_block}\n{stage_block}\n\n"
        f"Концепты автора, релевантные этой группе:\n{pool_lines}\n\n"
        f"Сгенерируй {limit} РАЗНЫХ ТЕМ для постов, каждая — одна короткая фраза "
        f"(5-12 слов), которая может быть подставлена в --hint у генератора контента. "
        f"Темы должны:\n"
        f"- покрывать разные углы (не повторяться),\n"
        f"- быть в продуктовом регистре (без мата, на «Вы»),\n"
        f"- избегать запрещённых формулировок: «истинная природа», «женская энергия», "
        f"«гарантия результата», «делай как я».\n\n"
        f"Верни ТОЛЬКО нумерованный список 1. … {limit}. … без преамбулы."
    )


def parse_topics(text: str) -> list[str]:
    """Достать строки вида `1. тема` / `10. тема`."""
    import re
    out: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)[.)]\s+(.+)$", line.strip())
        if m:
            out.append(m.group(2).strip().rstrip(".:;"))
    return out


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(
        description="Предложить 10 тем для генерации под заданную группу.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--voice", required=True,
                   help=f"Voice profile slug. {', '.join(list_voice_profiles())}")
    p.add_argument("--segment", default=None,
                   help=f"Segment slug. {', '.join(list_segments())}")
    p.add_argument("--psycho-type", default=None, dest="psycho_type",
                   help=f"Psycho type slug. {', '.join(list_psycho_types())}")
    p.add_argument("--hunt-stage", type=int, choices=[1, 2, 3, 4, 5], default=None, dest="hunt_stage")
    p.add_argument("--topic", nargs="*", default=[], dest="topics",
                   help="Топики из таксономии (marriage, partnership, …)")
    p.add_argument("--limit", type=int, default=10)
    args = p.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    voice = load_voice_profile(args.voice)
    segment = load_segment(args.segment) if args.segment else None
    psycho_type = load_psycho_type(args.psycho_type) if args.psycho_type else None

    conn = connect()
    try:
        pool = fetch_concepts_pool(conn, args.topics or None, args.hunt_stage, CONCEPT_POOL)
    finally:
        conn.close()

    if not pool:
        print("⚠ В корпусе нет концептов под эти фильтры. Попробуйте другую комбинацию.", file=sys.stderr)
        return 2

    prompt = build_prompt(pool, voice, segment, psycho_type, args.hunt_stage, args.limit)
    client = Anthropic(max_retries=4)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "text", None))
    topics = parse_topics(raw)

    print(f"Темы для {args.voice}" + (f" × {args.segment}" if args.segment else "") +
          (f" × {args.psycho_type}" if args.psycho_type else "") +
          (f" × hunt_stage={args.hunt_stage}" if args.hunt_stage else "") +
          (f" × topics={args.topics}" if args.topics else "") + ":\n")
    for i, t in enumerate(topics, 1):
        print(f"  {i:2d}. {t}")

    if not topics:
        print("(LLM не вернул нумерованный список — сырой ответ:)\n")
        print(raw)

    print(f"\n(modeling: {MODEL}, in={msg.usage.input_tokens}, out={msg.usage.output_tokens})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
