"""Главный pipeline: GenerationConfig → готовый ContentDraft в БД.

Шаги:
    1. Загрузить все 5 layers + стиль (lexicon / forbidden / raw_quotes)
    2. Retrieval (concepts + segments с фильтром topics/hunt_stage)
    3. Diversity hints (последние 5 драфтов для (channel, segment))
    4. Few-shot (последние 3 approved для (voice, channel, form, segment))
    5. Build system/user prompts (+ prompt_version)
    6. Call Anthropic API с prompt_caching (ephemeral) на стабильном префиксе
    7. Validators: apply_term_replacements → check_forbidden → check_provenance
       → check_lexicon_min → detect_pii
    8. calculate_cost из usage
    9. save_draft в content_drafts
    10. Вернуть ContentDraft

Streaming / Map-Reduce вынесены отдельными функциями (для длинных каналов
с requires_map_reduce=true; пока ни один канал такой не помечен — оставляем
hook'и под Phase 3+).
"""
from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from anthropic import Anthropic

from psy_helper.content_gen.config import (
    ContentDraft,
    DraftCost,
    GenerationConfig,
)
from psy_helper.content_gen.cost import calculate_cost
from psy_helper.content_gen.diversity import get_recent_drafts_hints
from psy_helper.content_gen.few_shot import pull_approved_examples
from psy_helper.content_gen.loaders import (
    load_channel,
    load_content_form,
    load_forbidden_topics,
    load_lexicon,
    load_psycho_type,
    load_raw_quotes,
    load_segment,
    load_voice_profile,
)
from psy_helper.content_gen.logging_config import get_logger
from psy_helper.content_gen.pii import detect_pii
from psy_helper.content_gen.prompts import (
    build_system_prompt,
    build_user_prompt,
    compute_prompt_hash,
    snapshot_config,
)
from psy_helper.content_gen.retrieval import retrieve_for_generation
from psy_helper.content_gen.storage import get_therapist_id, save_draft
from psy_helper.content_gen.validators import (
    apply_term_replacements,
    check_forbidden_phrases,
    check_length,
    check_lexicon_min,
    check_provenance,
    estimate_max_output_tokens,
)

if TYPE_CHECKING:
    import psycopg


log = get_logger(__name__)


# ─── Главный entry ────────────────────────────────────────────────────────────

def generate(
    cfg: GenerationConfig,
    conn: "psycopg.Connection",
    *,
    therapist_name: str = "Анна",
    max_tokens: int | None = None,
    save: bool = True,
) -> tuple[ContentDraft, str | None]:
    """Один проход: cfg → draft → (опционально save в БД).

    Возвращает (draft, draft_id_or_None).
    Если save=False, ничего не пишется в БД — полезно для CLI dry-run и тестов.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set in env")

    t0 = time.perf_counter()

    # 1. Loaders
    voice = load_voice_profile(cfg.voice_profile)
    channel = load_channel(cfg.channel)
    content_form = load_content_form(cfg.content_form)
    segment = load_segment(cfg.segment) if cfg.segment else None
    psycho_type = load_psycho_type(cfg.psycho_type) if cfg.psycho_type else None
    lexicon = load_lexicon()
    forbidden_topics = load_forbidden_topics()
    raw_quotes = load_raw_quotes(voice.sources.raw_quotes.path)

    log.info(
        "gen_start",
        voice=cfg.voice_profile, channel=cfg.channel, form=cfg.content_form,
        segment=cfg.segment, psycho_type=cfg.psycho_type, hunt_stage=cfg.hunt_stage,
        topics=cfg.topics, topic_hint=cfg.topic_hint,
    )

    # 2-4. Retrieval + diversity + few_shot
    ctx = retrieve_for_generation(cfg, conn)
    recent = get_recent_drafts_hints(conn, channel_slug=cfg.channel, segment_slug=cfg.segment)
    few_shot = pull_approved_examples(
        conn,
        voice_profile_slug=cfg.voice_profile,
        channel_slug=cfg.channel,
        content_form_slug=cfg.content_form,
        segment_slug=cfg.segment,
    )

    # 5. Prompts
    system_prompt = build_system_prompt(
        cfg,
        voice=voice, channel=channel, content_form=content_form,
        segment=segment, psycho_type=psycho_type,
        retrieval=ctx, lexicon=lexicon, forbidden_topics=forbidden_topics,
        raw_quotes=raw_quotes, recent_drafts=recent,
        few_shot_examples=few_shot,
    )
    user_prompt = build_user_prompt(cfg)
    prompt_version = compute_prompt_hash(system_prompt, user_prompt)
    config_snap = snapshot_config(cfg, voice, channel, content_form, segment, psycho_type)

    # 6. Anthropic call
    model = cfg.model_override or channel.preferred_model
    # max_tokens из канала (по char→token коэффициенту), не глобальный 2000
    effective_max_tokens = max_tokens or estimate_max_output_tokens(channel)

    client = Anthropic(max_retries=4)
    log.info(
        "anthropic_call",
        model=model, sys_chars=len(system_prompt), user_chars=len(user_prompt),
        max_tokens=effective_max_tokens,
        retrieved_concepts=len(ctx.concepts), retrieved_segments=len(ctx.segments),
        few_shot_count=len(few_shot), recent_drafts_count=len(recent),
    )

    msg = client.messages.create(
        model=model,
        max_tokens=effective_max_tokens,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw_text = "".join(b.text for b in msg.content if getattr(b, "text", None))

    # 7. Validators + transformations
    content = apply_term_replacements(raw_text, voice.term_replacements)
    forbidden_hits = check_forbidden_phrases(content, voice, forbidden_topics)
    bad_provenance = check_provenance(
        content, ctx.available_concept_tags, ctx.available_segment_tags,
    )
    lex_hits = check_lexicon_min(content, lexicon, content_form.lexicon_min)
    pii_flags = detect_pii(content)

    length_flag = check_length(content, channel)

    quality_flags: list[str] = []
    if length_flag:
        quality_flags.append(length_flag)
    if forbidden_hits:
        quality_flags.append(f"forbidden_phrases:{len(forbidden_hits)}")
    if bad_provenance:
        quality_flags.append(f"bad_provenance:{len(bad_provenance)}")
    if content_form.lexicon_min > 0 and lex_hits < content_form.lexicon_min:
        quality_flags.append(f"lexicon_below_min:{lex_hits}/{content_form.lexicon_min}")

    # 8. Cost
    cost = calculate_cost(msg.usage, model)

    duration_ms = int((time.perf_counter() - t0) * 1000)

    draft = ContentDraft(
        content=content,
        provenance=ctx.provenance_map,
        pii_flags=pii_flags + quality_flags,  # все флаги для review в одном поле
        prompt_version=prompt_version,
        config_snapshot=config_snap,
        model=model,
        cost=DraftCost(**cost),
        generation_duration_ms=duration_ms,
    )

    log.info(
        "gen_done",
        cost_usd=cost["cost_usd"],
        t_in=cost["tokens_input"], t_out=cost["tokens_output"],
        cache_read=cost["cache_read_tokens"], cache_write=cost["cache_creation_tokens"],
        duration_ms=duration_ms,
        forbidden_hits=len(forbidden_hits), bad_provenance=len(bad_provenance),
        lex_hits=lex_hits, pii_flags=len(pii_flags),
    )

    # 9. Save
    draft_id: str | None = None
    if save:
        therapist_id = get_therapist_id(conn, name=therapist_name)
        draft_id = save_draft(conn, therapist_id=therapist_id, cfg=cfg, draft=draft)
        draft.id = draft_id
        log.info("draft_saved", draft_id=draft_id)

    return draft, draft_id


# ─── Streaming variant (для UI) ───────────────────────────────────────────────

def generate_streaming(
    cfg: GenerationConfig,
    conn: "psycopg.Connection",
    *,
    therapist_name: str = "Анна",
    max_tokens: int | None = None,
):
    """Yields chunks of text as Anthropic streams them, then returns full ContentDraft.

    Usage:
        gen = generate_streaming(cfg, conn)
        for chunk in gen:
            print(chunk, end="", flush=True)
        draft = gen.value  # доступно через StopIteration в обычном виде

    Для Streamlit UI: подписаться на yield → обновлять st.write по чанку.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set in env")

    t0 = time.perf_counter()

    voice = load_voice_profile(cfg.voice_profile)
    channel = load_channel(cfg.channel)
    content_form = load_content_form(cfg.content_form)
    segment = load_segment(cfg.segment) if cfg.segment else None
    psycho_type = load_psycho_type(cfg.psycho_type) if cfg.psycho_type else None
    lexicon = load_lexicon()
    forbidden_topics = load_forbidden_topics()
    raw_quotes = load_raw_quotes(voice.sources.raw_quotes.path)

    ctx = retrieve_for_generation(cfg, conn)
    recent = get_recent_drafts_hints(conn, channel_slug=cfg.channel, segment_slug=cfg.segment)
    few_shot = pull_approved_examples(
        conn,
        voice_profile_slug=cfg.voice_profile,
        channel_slug=cfg.channel,
        content_form_slug=cfg.content_form,
        segment_slug=cfg.segment,
    )

    system_prompt = build_system_prompt(
        cfg,
        voice=voice, channel=channel, content_form=content_form,
        segment=segment, psycho_type=psycho_type,
        retrieval=ctx, lexicon=lexicon, forbidden_topics=forbidden_topics,
        raw_quotes=raw_quotes, recent_drafts=recent, few_shot_examples=few_shot,
    )
    user_prompt = build_user_prompt(cfg)
    prompt_version = compute_prompt_hash(system_prompt, user_prompt)
    config_snap = snapshot_config(cfg, voice, channel, content_form, segment, psycho_type)

    model = cfg.model_override or channel.preferred_model
    effective_max_tokens = max_tokens or estimate_max_output_tokens(channel)
    client = Anthropic(max_retries=4)
    log.info("anthropic_stream_start", model=model, max_tokens=effective_max_tokens)

    chunks: list[str] = []
    final_message = None
    with client.messages.stream(
        model=model,
        max_tokens=effective_max_tokens,
        system=[{
            "type": "text", "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
            yield text
        final_message = stream.get_final_message()

    raw_text = "".join(chunks)
    content = apply_term_replacements(raw_text, voice.term_replacements)

    forbidden_hits = check_forbidden_phrases(content, voice, forbidden_topics)
    bad_provenance = check_provenance(content, ctx.available_concept_tags, ctx.available_segment_tags)
    lex_hits = check_lexicon_min(content, lexicon, content_form.lexicon_min)
    pii_flags = detect_pii(content)
    length_flag = check_length(content, channel)

    quality_flags: list[str] = []
    if length_flag:
        quality_flags.append(length_flag)
    if forbidden_hits:
        quality_flags.append(f"forbidden_phrases:{len(forbidden_hits)}")
    if bad_provenance:
        quality_flags.append(f"bad_provenance:{len(bad_provenance)}")
    if content_form.lexicon_min > 0 and lex_hits < content_form.lexicon_min:
        quality_flags.append(f"lexicon_below_min:{lex_hits}/{content_form.lexicon_min}")

    cost = calculate_cost(final_message.usage, model)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    draft = ContentDraft(
        content=content,
        provenance=ctx.provenance_map,
        pii_flags=pii_flags + quality_flags,
        prompt_version=prompt_version,
        config_snapshot=config_snap,
        model=model,
        cost=DraftCost(**cost),
        generation_duration_ms=duration_ms,
    )

    therapist_id = get_therapist_id(conn, name=therapist_name)
    draft_id = save_draft(conn, therapist_id=therapist_id, cfg=cfg, draft=draft)
    draft.id = draft_id

    log.info(
        "gen_stream_done",
        draft_id=draft_id, cost_usd=cost["cost_usd"], duration_ms=duration_ms,
    )

    return draft  # для PEP-380 совместимости (через .value at StopIteration)
