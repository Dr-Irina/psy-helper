"""HTTP-мостик корпуса Ани в контент-завод (и любой внешний потребитель).

FastAPI поверх готового retrieval (psy_helper.content_gen.retrieval): по теме
запроса возвращает релевантные концепты Ани **с дословными цитатами** (её голос,
после reranker) + готовый knowledge-блок для инъекции в генерацию завода как
{knowledge}. Запрос эмбеддится той же e5-large, что и корпус.

Контракт (generic, завод-agnostic):
    POST /retrieve
      { "query": "...", "filters": {"topics": [...], "hunt_stage": 2, "types": [...]},
        "k": 12, "pool": 50 }
    → { "items": [{type, name, description, salience, quotes:[{text}]}],
        "signature": [{type, phrase}],
        "knowledge_block": "<готовый markdown для {knowledge}>" }

Запуск (в docker, рядом с postgres):
    uvicorn psy_helper.api.retrieval_service:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from pgvector.psycopg import register_vector
from pydantic import BaseModel, Field

from psy_helper.content_gen import retrieval as R
from psy_helper.db.connection import connect

app = FastAPI(title="psy-helper retrieval", version="1.0")


class Filters(BaseModel):
    topics: list[str] | None = None
    hunt_stage: int | None = None
    types: list[str] | None = None  # term/technique/claim/... — пост-фильтр


class RetrieveRequest(BaseModel):
    query: str
    filters: Filters = Field(default_factory=Filters)
    k: int = 12          # сколько концептов вернуть (после rerank)
    pool: int = 50       # широкий пул до rerank
    rerank: bool = True


class QuoteOut(BaseModel):
    text: str
    speaker: str | None = None


class ConceptOut(BaseModel):
    type: str
    name: str
    description: str
    salience: int
    quotes: list[QuoteOut]


class SignatureOut(BaseModel):
    type: str
    phrase: str


class RetrieveResponse(BaseModel):
    items: list[ConceptOut]
    signature: list[SignatureOut]
    knowledge_block: str


def _knowledge_block(concepts: list, signature: list) -> str:
    """Готовый текст для инъекции в {knowledge} завода — со СМЫСЛОМ и ГОЛОСОМ Ани."""
    block = R.format_concepts_for_prompt(concepts)
    if signature:
        qs = [s["phrase"] for s in signature if s["type"] == "question"][:5]
        ms = [s["phrase"] for s in signature if s["type"] == "metaphor"][:5]
        if qs:
            block += "\n\nФирменные вопросы автора: " + " / ".join(f"«{q}»" for q in qs)
        if ms:
            block += "\nМетафоры автора: " + " / ".join(f"«{m}»" for m in ms)
    return block


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/retrieve", response_model=RetrieveResponse)
def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    emb = R.embed_query(req.query)
    with connect() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            pool = R._hybrid_concepts_filtered(
                cur, req.query, emb,
                topics=req.filters.topics or None,
                hunt_stage=req.filters.hunt_stage,
                limit=req.pool if req.rerank else req.k,
            )
            signature = R.retrieve_signature(cur, req.query, emb, limit=8)

    concepts = R.rerank_concepts(req.query, pool, req.k) if req.rerank else pool[: req.k]
    if req.filters.types:
        allowed = set(req.filters.types)
        concepts = [c for c in concepts if c.type in allowed] or concepts

    items = [
        ConceptOut(
            type=c.type, name=c.name, description=c.description, salience=c.salience,
            quotes=[QuoteOut(text=(q.get("text") if isinstance(q, dict) else str(q)),
                             speaker=(q.get("speaker") if isinstance(q, dict) else None))
                    for q in (c.quotes or [])],
        )
        for c in concepts
    ]
    return RetrieveResponse(
        items=items,
        signature=[SignatureOut(type=s["type"], phrase=s["phrase"]) for s in signature],
        knowledge_block=_knowledge_block(concepts, signature),
    )
