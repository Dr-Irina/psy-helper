"""Retrieval для генератора: гибридный поиск + фильтры по topics/hunt_stages.

Зачем отдельный модуль вместо прямого вызова psy_helper.search:
    - старый search.py не знает про concepts.topics / concepts.hunt_stages
      (миграция 003 пришла позже)
    - тут собираем query_text из 5 layers (segment.main_message + pain_phrases +
      topic_hint) и присваиваем provenance-теги c1..cN / s1..sM
    - возвращаем готовый материал + map для footnote-проверки

UUID-теги: в БД id = UUID. В промт отдаём короткие c1..c15 / s1..s5,
чтобы LLM мог писать footnotes [^c3] без вставки UUID в текст. Map хранится
в content_drafts.provenance JSONB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pgvector.psycopg import register_vector

from psy_helper.content_gen.config import GenerationConfig
from psy_helper.content_gen.loaders import load_segment

if TYPE_CHECKING:
    import psycopg

# E5-family требует префикс "query:" для запросов.
QUERY_PREFIX = "query: "

# RRF параметры — повторяют psy_helper.search для консистентности
RRF_K = 60
TOP_PER_SOURCE = 50


# ─── Lazy embedder singleton ──────────────────────────────────────────────────

_MODEL = None
_MODEL_NAME = "intfloat/multilingual-e5-large"


def get_embedder():
    """Lazy-init модель эмбеддингов. ~2GB RAM, ~10s init."""
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(_MODEL_NAME)
    return _MODEL


def embed_query(text: str):
    """1024-dim numpy array. Префикс query: уже добавляется."""
    return get_embedder().encode(QUERY_PREFIX + text, normalize_embeddings=True)


# ─── Reranker (cross-encoder) ─────────────────────────────────────────────────
# Гибрид (BM25+vector) даёт грубый recall; cross-encoder смотрит на (запрос, концепт)
# вместе и точнее отбирает top-K. Модель локальная, мультиязычная.

_RERANKER = None
_RERANKER_NAME = "BAAI/bge-reranker-v2-m3"


def get_reranker():
    global _RERANKER
    if _RERANKER is None:
        from sentence_transformers import CrossEncoder
        _RERANKER = CrossEncoder(_RERANKER_NAME)
    return _RERANKER


def rerank_concepts(query_text: str, items: list, top_k: int) -> list:
    """Переранжировать пул концептов cross-encoder'ом, вернуть top_k. Теги c1..cK
    перевыставляются ПОСЛЕ rerank (provenance-map строится по новому порядку)."""
    if not items:
        return items
    ce = get_reranker()
    pairs = [(query_text, f"{c.name}. {c.description}") for c in items]
    scores = ce.predict(pairs)
    ranked = [c for c, _ in sorted(zip(items, scores), key=lambda x: float(x[1]), reverse=True)]
    out = ranked[:top_k]
    for i, c in enumerate(out):
        c.tag = f"c{i+1}"  # перетегировать по новому порядку
    return out


# ─── Result types ─────────────────────────────────────────────────────────────

@dataclass
class ConceptItem:
    tag: str              # "c1", "c2" ... — то, что увидит LLM в промте
    uuid: str
    name: str
    type: str
    description: str
    score: float
    quotes: list = field(default_factory=list)  # [{text, speaker}] — дословный голос Ани
    salience: int = 2  # значимость концепта 1-3
    source_segments: list = field(default_factory=list)  # UUID родительских блоков (parent-child)


@dataclass
class SegmentItem:
    tag: str              # "s1", "s2" ...
    uuid: str
    title: str | None
    summary: str | None
    text: str
    source_file: str
    score: float


@dataclass
class RetrievalContext:
    concepts: list[ConceptItem] = field(default_factory=list)
    segments: list[SegmentItem] = field(default_factory=list)
    query_text: str = ""
    signature: list = field(default_factory=list)  # фирменные вопросы/метафоры под тему

    @property
    def provenance_map(self) -> dict[str, str]:
        """{ "c1": uuid, "s1": uuid } для сохранения в content_drafts.provenance."""
        m: dict[str, str] = {}
        for c in self.concepts:
            m[c.tag] = c.uuid
        for s in self.segments:
            m[s.tag] = s.uuid
        return m

    @property
    def available_concept_tags(self) -> list[int]:
        """Для check_provenance — список разрешённых числовых тегов."""
        return [int(c.tag[1:]) for c in self.concepts]

    @property
    def available_segment_tags(self) -> list[int]:
        return [int(s.tag[1:]) for s in self.segments]


# ─── Query builder ────────────────────────────────────────────────────────────

def build_query_text(cfg: GenerationConfig) -> str:
    """Собирает текст-запрос для retrieval из всех контекстных полей.

    Приоритет: явный topic_hint > main_message сегмента + pain_phrases > topics.
    """
    parts: list[str] = []

    if cfg.topic_hint:
        parts.append(cfg.topic_hint)

    if cfg.segment:
        seg = load_segment(cfg.segment)
        if seg.main_message:
            parts.append(seg.main_message.strip())
        if seg.pain_phrases:
            parts.append(" ".join(seg.pain_phrases))

    if cfg.topics:
        parts.append(" ".join(cfg.topics))

    return "\n".join(parts) or "общее по методу"


# ─── Hybrid search with topic/hunt_stage filters ──────────────────────────────

def _hybrid_concepts_filtered(
    cur,
    query_text: str,
    query_embedding,
    *,
    topics: list[str] | None,
    hunt_stage: int | None,
    limit: int,
) -> list[ConceptItem]:
    """Гибрид с фильтрами по новым колонкам concepts.topics / hunt_stages."""
    where_extra = []
    params: dict[str, Any] = {
        "qtext": query_text,
        "qvec": query_embedding,
        "top": TOP_PER_SOURCE,
        "limit": limit,
    }
    if topics:
        where_extra.append("topics && %(topics)s")
        params["topics"] = topics
    if hunt_stage is not None:
        where_extra.append("%(stage)s = ANY(hunt_stages)")
        params["stage"] = hunt_stage

    where_clause = ("AND " + " AND ".join(where_extra)) if where_extra else ""

    sql = f"""
    WITH bm25 AS (
      SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(search_tsv, q) DESC) AS rk
      FROM concepts, plainto_tsquery('russian', %(qtext)s) q
      WHERE search_tsv @@ q {where_clause}
      ORDER BY rk
      LIMIT %(top)s
    ),
    vec AS (
      SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> %(qvec)s) AS rk
      FROM concepts
      WHERE embedding IS NOT NULL {where_clause}
      ORDER BY rk
      LIMIT %(top)s
    ),
    fused AS (
      SELECT id,
             SUM(1.0 / ({RRF_K} + rk)) AS score
      FROM (
        SELECT id, rk FROM bm25
        UNION ALL
        SELECT id, rk FROM vec
      ) u
      GROUP BY id
    )
    SELECT c.id::text, c.name, c.type, COALESCE(c.description, ''), f.score,
           COALESCE(c.quotes, '[]'::jsonb), COALESCE(c.source_segments, '{{}}'),
           COALESCE(c.salience, 2)
    FROM fused f
    JOIN concepts c ON c.id = f.id
    ORDER BY f.score DESC
    LIMIT %(limit)s
    """
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [
        ConceptItem(tag=f"c{i+1}", uuid=r[0], name=r[1], type=r[2], description=r[3],
                    score=float(r[4]), quotes=r[5] or [],
                    source_segments=[str(s) for s in (r[6] or [])], salience=int(r[7]))
        for i, r in enumerate(rows)
    ]


def _parent_segments(cur, concepts: list, existing_ids: set, *, limit: int) -> list[SegmentItem]:
    """Parent-child: родительские блоки top-концептов (для полного контекста),
    которых ещё нет среди ретривнутых сегментов."""
    want: list[str] = []
    for c in concepts:
        for sid in (c.source_segments or []):
            if sid not in existing_ids and sid not in want:
                want.append(sid)
    want = want[:limit]
    if not want:
        return []
    cur.execute(
        """
        SELECT cs.id::text, cs.title, cs.summary, cs.text, rt.source_file
        FROM clean_segments cs JOIN raw_transcripts rt ON rt.id = cs.raw_id
        WHERE cs.id = ANY(%s)
        """,
        (want,),
    )
    return [
        SegmentItem(tag="", uuid=r[0], title=r[1], summary=r[2], text=r[3],
                    source_file=r[4], score=0.0)
        for r in cur.fetchall()
    ]


def retrieve_signature(cur, query_text: str, query_embedding, *, limit: int = 8) -> list[dict]:
    """Фирменные вопросы/метафоры Ани под тему запроса (тип question/metaphor).

    Топик-релевантны и варьируются по теме → в каждом посте разные формулировки,
    а не статичный top-8. Возвращает [{type, name, quote}].
    """
    sql = f"""
    WITH bm25 AS (
      SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(search_tsv, q) DESC) AS rk
      FROM concepts, plainto_tsquery('russian', %(qtext)s) q
      WHERE search_tsv @@ q AND type IN ('question','metaphor')
      ORDER BY rk LIMIT %(top)s
    ),
    vec AS (
      SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> %(qvec)s) AS rk
      FROM concepts
      WHERE embedding IS NOT NULL AND type IN ('question','metaphor')
      ORDER BY rk LIMIT %(top)s
    ),
    fused AS (
      SELECT id, SUM(1.0 / ({RRF_K} + rk)) AS score
      FROM (SELECT id, rk FROM bm25 UNION ALL SELECT id, rk FROM vec) u
      GROUP BY id
    )
    SELECT c.type, c.name, COALESCE(c.quotes, '[]'::jsonb)
    FROM fused f JOIN concepts c ON c.id = f.id
    ORDER BY f.score DESC LIMIT %(limit)s
    """
    cur.execute(sql, {"qtext": query_text, "qvec": query_embedding,
                      "top": TOP_PER_SOURCE, "limit": limit})
    out = []
    for ctype, name, quotes in cur.fetchall():
        phrase = (quotes[0].get("text") if quotes and isinstance(quotes[0], dict) else None) or name
        out.append({"type": ctype, "name": name, "phrase": phrase.strip()})
    return out


def _hybrid_segments(
    cur,
    query_text: str,
    query_embedding,
    *,
    limit: int,
) -> list[SegmentItem]:
    """clean_segments не имеют topics — фильтруем только через relevance."""
    sql = f"""
    WITH bm25 AS (
      SELECT cs.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(cs.search_tsv, q) DESC) AS rk
      FROM clean_segments cs, plainto_tsquery('russian', %(qtext)s) q
      WHERE cs.search_tsv @@ q
      ORDER BY rk LIMIT %(top)s
    ),
    vec AS (
      SELECT cs.id,
             ROW_NUMBER() OVER (ORDER BY se.embedding <=> %(qvec)s) AS rk
      FROM clean_segments cs
      JOIN segment_embeddings se ON se.segment_id = cs.id
      ORDER BY rk LIMIT %(top)s
    ),
    fused AS (
      SELECT id, SUM(1.0 / ({RRF_K} + rk)) AS score
      FROM (SELECT id, rk FROM bm25 UNION ALL SELECT id, rk FROM vec) u
      GROUP BY id
    )
    SELECT cs.id::text, cs.title, cs.summary, cs.text,
           rt.source_file, f.score
    FROM fused f
    JOIN clean_segments cs ON cs.id = f.id
    JOIN raw_transcripts rt ON rt.id = cs.raw_id
    ORDER BY f.score DESC
    LIMIT %(limit)s
    """
    cur.execute(sql, {
        "qtext": query_text, "qvec": query_embedding,
        "top": TOP_PER_SOURCE, "limit": limit,
    })
    rows = cur.fetchall()
    return [
        SegmentItem(
            tag=f"s{i+1}", uuid=r[0], title=r[1], summary=r[2], text=r[3],
            source_file=r[4], score=float(r[5]),
        )
        for i, r in enumerate(rows)
    ]


# ─── Top-level entry ──────────────────────────────────────────────────────────

def retrieve_for_generation(
    cfg: GenerationConfig,
    conn: "psycopg.Connection",
    *,
    concept_limit: int = 15,
    segment_limit: int = 5,
    concept_pool: int = 50,
    use_reranker: bool = True,
    parent_limit: int = 3,
) -> RetrievalContext:
    """Главный entry — вернуть весь retrieval-контекст для одного config'а.

    Двухэтапно: гибрид достаёт широкий пул (concept_pool) по recall, затем
    cross-encoder переранжирует в concept_limit по precision.
    """
    register_vector(conn)
    query_text = build_query_text(cfg)
    query_emb = embed_query(query_text)

    with conn.cursor() as cur:
        pool = _hybrid_concepts_filtered(
            cur, query_text, query_emb,
            topics=cfg.topics or None,
            hunt_stage=cfg.hunt_stage,
            limit=concept_pool if use_reranker else concept_limit,
        )
        segments = _hybrid_segments(
            cur, query_text, query_emb, limit=segment_limit,
        )
        signature = retrieve_signature(cur, query_text, query_emb, limit=8)

    concepts = (rerank_concepts(query_text, pool, concept_limit)
                if use_reranker else pool[:concept_limit])

    # Parent-child: добрать родительские блоки top-концептов для полного контекста.
    if parent_limit:
        existing = {s.uuid for s in segments}
        with conn.cursor() as cur:
            parents = _parent_segments(cur, concepts[:5], existing, limit=parent_limit)
        segments = segments + parents
        for i, s in enumerate(segments):  # перетегировать s1..sM сквозным порядком
            s.tag = f"s{i+1}"

    return RetrievalContext(
        concepts=concepts, segments=segments, query_text=query_text, signature=signature,
    )


# ─── Formatters для промта ────────────────────────────────────────────────────

def format_concepts_for_prompt(items: list[ConceptItem], *, max_quotes: int = 2) -> str:
    """Блок текста, который вставляется в системный промт.

    Под каждым концептом — дословные цитаты Ани (её голос). Разные темы тянут
    разные концепты → разные цитаты, поэтому формулировки в постах варьируются.
    """
    if not items:
        return "(нет релевантных концептов в корпусе)"
    lines = []
    for c in items:
        desc = c.description.strip().replace("\n", " ")[:300]
        lines.append(f"[{c.tag}] ({c.type}) {c.name} — {desc}")
        for q in (c.quotes or [])[:max_quotes]:
            txt = (q.get("text") if isinstance(q, dict) else str(q)) or ""
            txt = txt.strip().replace("\n", " ")[:200]
            if txt:
                lines.append(f"      голосом автора: «{txt}»")
    return "\n".join(lines)


def format_segments_for_prompt(items: list[SegmentItem]) -> str:
    if not items:
        return "(нет релевантных смысловых блоков)"
    lines = []
    for s in items:
        title = (s.title or s.summary or "").strip().replace("\n", " ")[:80]
        body = s.text.strip().replace("\n", " ")[:400]
        lines.append(f"[{s.tag}] «{title}» — {body}")
    return "\n".join(lines)
