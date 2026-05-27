"""Universal content engine для контент-воронки Академии Супружества.

Композиция 5 слоёв (voice_profile × segment × psycho_type × channel × content_form)
+ параметры (hunt_stage, topics, topic_hint) → LLM → черновик в content_drafts.

Подмодули (заполняются по Step 3-6 плана):
    config         — Pydantic-модели слоёв + GenerationConfig
    loaders        — load_voice_profile(slug), load_segment, ... (LRU-кэш)
    retrieval      — wrapper над psy_helper.search с фильтрами topics/hunt_stages
    diversity      — anti-similarity к past drafts
    prompts        — BASE_TEMPLATE + FORM_MODIFIERS
    generator      — главный pipeline (sync + streaming + Map-Reduce)
    validators     — provenance / forbidden / term_replacements
    pii            — regex на known names / phones / emails
    cost           — calculate_cost(usage, model)
    few_shot       — self-improving loop из approved drafts
    storage        — save_draft / load_draft / update_status
    logging_config — structured JSON logging
"""
