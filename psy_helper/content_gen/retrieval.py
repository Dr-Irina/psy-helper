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


# ─── Result types ─────────────────────────────────────────────────────────────

@dataclass
class ConceptItem:
    tag: str              # "c1", "c2" ... — то, что увидит LLM в промте
    uuid: str
    name: str
    type: str
    description: str
    score: float


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
    SELECT c.id::text, c.name, c.type, COALESCE(c.description, ''), f.score
    FROM fused f
    JOIN concepts c ON c.id = f.id
    ORDER BY f.score DESC
    LIMIT %(limit)s
    """
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [
        ConceptItem(tag=f"c{i+1}", uuid=r[0], name=r[1], type=r[2], description=r[3], score=float(r[4]))
        for i, r in enumerate(rows)
    ]


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
) -> RetrievalContext:
    """Главный entry — вернуть весь retrieval-контекст для одного config'а."""
    register_vector(conn)
    query_text = build_query_text(cfg)
    query_emb = embed_query(query_text)

    with conn.cursor() as cur:
        concepts = _hybrid_concepts_filtered(
            cur, query_text, query_emb,
            topics=cfg.topics or None,
            hunt_stage=cfg.hunt_stage,
            limit=concept_limit,
        )
        segments = _hybrid_segments(
            cur, query_text, query_emb, limit=segment_limit,
        )

    return RetrievalContext(concepts=concepts, segments=segments, query_text=query_text)


# ─── Formatters для промта ────────────────────────────────────────────────────

def format_concepts_for_prompt(items: list[ConceptItem]) -> str:
    """Блок текста, который вставляется в системный промт."""
    if not items:
        return "(нет релевантных концептов в корпусе)"
    lines = []
    for c in items:
        desc = c.description.strip().replace("\n", " ")[:300]
        lines.append(f"[{c.tag}] ({c.type}) {c.name} — {desc}")
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
