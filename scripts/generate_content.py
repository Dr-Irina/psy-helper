"""CLI-обёртка над psy_helper.content_gen.generator.generate().

Запуск:
    docker compose run --rm app python scripts/generate_content.py \\
        --voice anna_product --channel tg_post --form storytelling \\
        --segment tired_wife --psycho-type patient --hunt-stage 2 \\
        --topic marriage --hint "границы в супружестве"

Флаги:
    --no-save        — не записывать в content_drafts (dry-run для отладки промта)
    --model haiku    — переопределить preferred_model канала на Haiku 4.5
    --model sonnet   — то же на Sonnet 4.6
    --max-tokens N   — переопределить расчётный лимит из канала
    --topic X Y Z    — список топиков (можно повторять флаг или передать через пробел)

Выход: текст черновика + сводка (id / cost / flags). При --no-save id = None.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from psy_helper.content_gen.config import GenerationConfig
from psy_helper.content_gen.generator import generate
from psy_helper.content_gen.loaders import (
    list_channels,
    list_content_forms,
    list_psycho_types,
    list_segments,
    list_voice_profiles,
)
from psy_helper.content_gen.logging_config import setup_logging
from psy_helper.db.connection import connect


MODEL_ALIASES = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-7",
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Сгенерировать один черновик контента через layered config.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--voice", required=True,
                   help=f"Voice profile slug. Доступно: {', '.join(list_voice_profiles())}")
    p.add_argument("--channel", required=True,
                   help=f"Channel slug. Доступно: {', '.join(list_channels())}")
    p.add_argument("--form", required=True,
                   help=f"Content form slug. Доступно: {', '.join(list_content_forms())}")
    p.add_argument("--segment", default=None,
                   help=f"Segment slug. Доступно: {', '.join(list_segments())}")
    p.add_argument("--psycho-type", default=None, dest="psycho_type",
                   help=f"Psycho type slug. Доступно: {', '.join(list_psycho_types())}")
    p.add_argument("--hunt-stage", type=int, choices=[1, 2, 3, 4, 5], default=None,
                   dest="hunt_stage", help="Ступень лестницы Ханта")
    p.add_argument("--topic", nargs="*", default=[], dest="topics",
                   help="Список топиков (marriage, partnership, children, …)")
    p.add_argument("--hint", default=None, dest="topic_hint",
                   help="Конкретная тема-затравка для черновика")
    p.add_argument("--model", default=None, choices=list(MODEL_ALIASES),
                   help="Переопределить модель канала (haiku/sonnet/opus)")
    p.add_argument("--max-tokens", type=int, default=None, dest="max_tokens",
                   help="Переопределить расчётный max_tokens (по умолчанию — из канала)")
    p.add_argument("--no-save", action="store_true",
                   help="Не записывать в content_drafts (dry-run)")
    p.add_argument("--output", type=Path, default=None,
                   help="Записать готовый текст ещё и в файл .md")
    p.add_argument("--quiet", action="store_true",
                   help="Не печатать JSON-логи — только итоговый отчёт")
    return p


def main() -> int:
    load_dotenv()
    args = _build_parser().parse_args()

    if not args.quiet:
        setup_logging()

    cfg = GenerationConfig(
        voice_profile=args.voice,
        channel=args.channel,
        content_form=args.form,
        segment=args.segment,
        psycho_type=args.psycho_type,
        hunt_stage=args.hunt_stage,
        topics=args.topics,
        topic_hint=args.topic_hint,
        model_override=MODEL_ALIASES.get(args.model) if args.model else None,
    )

    conn = connect()
    try:
        draft, draft_id = generate(
            cfg, conn,
            save=not args.no_save,
            max_tokens=args.max_tokens,
        )
    finally:
        conn.close()

    sep = "─" * 70
    print(sep)
    print("CONTENT:")
    print(sep)
    print(draft.content)
    print(sep)
    print(f"id           : {draft_id or '(not saved — --no-save)'}")
    print(f"model        : {draft.model}")
    print(f"length       : {len(draft.content)} chars")
    print(f"cost         : ${draft.cost.cost_usd:.4f}")
    print(f"tokens       : in={draft.cost.tokens_input}, out={draft.cost.tokens_output},"
          f" cache_w={draft.cost.cache_creation_tokens}, cache_r={draft.cost.cache_read_tokens}")
    print(f"duration     : {draft.generation_duration_ms} ms")
    print(f"prompt_ver   : {draft.prompt_version}")
    print(f"flags        : {draft.pii_flags or '(чисто)'}")
    print(f"provenance   : {len(draft.provenance)} ссылок на корпус")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(draft.content, encoding="utf-8")
        print(f"\n✓ Saved markdown → {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
