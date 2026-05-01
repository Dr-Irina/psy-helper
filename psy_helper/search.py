"""Гибридный поиск: BM25 (Postgres tsvector) + векторный, объединение через RRF.

Reciprocal Rank Fusion (k=60): score(d) = Σ 1 / (k + rank_i(d))
— стандартный приём объединения списков top-K из разных источников.
Не зависит от шкал скоров, устойчив к выбросам.

Используется из Streamlit-UI и (позже) из бота МVP-2.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RRF_K = 60
TOP_PER_SOURCE = 50  # сколько брать из каждого источника до фьюжна


@dataclass
class ConceptHit:
    id: str
    name: str
    type: str
    description: str | None
    sources_count: int | None
    score: float
    bm25_rank: int | None
    vec_rank: int | None


@dataclass
class SegmentHit:
    id: str
    title: str | None
    summary: str | None
    text: str
    start_ts: float
    end_ts: float
    source_file: str
    score: float
    bm25_rank: int | None
    vec_rank: int | None


def hybrid_search_concepts(
    cur,
    query_text: str,
    query_embedding,
    *,
    types: list[str] | None = None,
    limit: int = 12,
) -> list[ConceptHit]:
    """Гибридный поиск по concepts. query_text для BM25, embedding для векторного."""
    type_filter = ""
    type_params: list[Any] = []
    if types:
        type_filter = "AND type = ANY(%(types)s)"
        type_params = [types]

    sql = f"""
    WITH bm25 AS (
      SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(search_tsv, q) DESC) AS rk
      FROM concepts, plainto_tsquery('russian', %(qtext)s) q
      WHERE search_tsv @@ q {type_filter}
      ORDER BY rk
      LIMIT %(top)s
    ),
    vec AS (
      SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> %(qvec)s) AS rk
      FROM concepts
      WHERE embedding IS NOT NULL {type_filter}
      ORDER BY rk
      LIMIT %(top)s
    ),
    fused AS (
      SELECT id,
             SUM(1.0 / ({RRF_K} + rk)) AS score,
             MAX(CASE WHEN src='b' THEN rk END) AS bm25_rank,
             MAX(CASE WHEN src='v' THEN rk END) AS vec_rank
      FROM (
        SELECT id, rk, 'b' AS src FROM bm25
        UNION ALL
        SELECT id, rk, 'v' AS src FROM vec
      ) u
      GROUP BY id
    )
    SELECT c.id::text, c.name, c.type, c.description,
           array_length(c.source_segments, 1) AS sources_count,
           f.score, f.bm25_rank, f.vec_rank
    FROM fused f
    JOIN concepts c ON c.id = f.id
    ORDER BY f.score DESC
    LIMIT %(limit)s
    """
    params = {
        "qtext": query_text,
        "qvec": query_embedding,
        "top": TOP_PER_SOURCE,
        "limit": limit,
    }
    if types:
        params["types"] = types

    cur.execute(sql, params)
    return [ConceptHit(*row) for row in cur.fetchall()]


def hybrid_search_segments(
    cur,
    query_text: str,
    query_embedding,
    *,
    limit: int = 6,
) -> list[SegmentHit]:
    sql = f"""
    WITH bm25 AS (
      SELECT cs.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(cs.search_tsv, q) DESC) AS rk
      FROM clean_segments cs, plainto_tsquery('russian', %(qtext)s) q
      WHERE cs.search_tsv @@ q
      ORDER BY rk
      LIMIT %(top)s
    ),
    vec AS (
      SELECT cs.id,
             ROW_NUMBER() OVER (ORDER BY se.embedding <=> %(qvec)s) AS rk
      FROM clean_segments cs
      JOIN segment_embeddings se ON se.segment_id = cs.id
      ORDER BY rk
      LIMIT %(top)s
    ),
    fused AS (
      SELECT id,
             SUM(1.0 / ({RRF_K} + rk)) AS score,
             MAX(CASE WHEN src='b' THEN rk END) AS bm25_rank,
             MAX(CASE WHEN src='v' THEN rk END) AS vec_rank
      FROM (
        SELECT id, rk, 'b' AS src FROM bm25
        UNION ALL
        SELECT id, rk, 'v' AS src FROM vec
      ) u
      GROUP BY id
    )
    SELECT cs.id::text, cs.title, cs.summary, cs.text, cs.start_ts, cs.end_ts,
           rt.source_file, f.score, f.bm25_rank, f.vec_rank
    FROM fused f
    JOIN clean_segments cs ON cs.id = f.id
    JOIN raw_transcripts rt ON rt.id = cs.raw_id
    ORDER BY f.score DESC
    LIMIT %(limit)s
    """
    cur.execute(
        sql,
        {
            "qtext": query_text,
            "qvec": query_embedding,
            "top": TOP_PER_SOURCE,
            "limit": limit,
        },
    )
    return [SegmentHit(*row) for row in cur.fetchall()]
