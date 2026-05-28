"""Залить data/style/lexicon.json в lexicon_items с эмбеддингами.

Идемпотентный: TRUNCATE + INSERT в одной транзакции. JSON остаётся
источником истины — БД это только индексированная копия для поиска.

Эмбеддинги: e5-large локально с префиксом "passage: " (чтобы запросы
с префиксом "query: " находили семантически близкие фразы).

Запуск:
    docker compose run --rm app python scripts/ingest_lexicon.py
    # или
    docker exec psy-helper-ui-1 python /app/scripts/ingest_lexicon.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from psy_helper.db.connection import connect

LEXICON_PATH = Path("data/style/lexicon.json")
MODEL_NAME = "intfloat/multilingual-e5-large"
PASSAGE_PREFIX = "passage: "
BATCH_SIZE = 64


def build_passage(item: dict) -> str:
    """То что эмбеддим: фраза + описание (если есть)."""
    phrase = (item.get("phrase") or "").strip()
    desc = (item.get("description") or "").strip()
    if desc:
        return f"{PASSAGE_PREFIX}{phrase}. {desc}"
    return f"{PASSAGE_PREFIX}{phrase}"


def main() -> int:
    load_dotenv()
    if not LEXICON_PATH.exists():
        print(f"ERROR: {LEXICON_PATH} not found", file=sys.stderr)
        return 1

    lex = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    questions = lex.get("questions", [])
    metaphors = lex.get("metaphors", [])
    print(f"Loaded: {len(questions)} questions + {len(metaphors)} metaphors")

    print(f"Loading {MODEL_NAME} (~10 sec)…", flush=True)
    model = SentenceTransformer(MODEL_NAME)

    rows: list[tuple[str, str, str, int, list]] = []
    for items, kind in ((questions, "question"), (metaphors, "metaphor")):
        passages = [build_passage(it) for it in items]
        print(f"Embedding {len(passages)} {kind}s…", flush=True)
        embeddings = model.encode(
            passages, batch_size=BATCH_SIZE, normalize_embeddings=True,
            show_progress_bar=False,
        )
        for it, emb in zip(items, embeddings):
            rows.append((
                kind,
                (it.get("phrase") or "").strip(),
                (it.get("description") or "").strip() or None,
                int(it.get("mentions") or 0),
                emb.tolist(),
            ))

    conn = connect()
    register_vector(conn)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE lexicon_items")
        cur.executemany(
            """
            INSERT INTO lexicon_items (kind, phrase, description, mentions, embedding)
            VALUES (%s, %s, %s, %s, %s)
            """,
            rows,
        )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT kind, COUNT(*) FROM lexicon_items GROUP BY kind")
        for kind, n in cur.fetchall():
            print(f"  {kind:10} : {n}")

    print("✓ Готово")
    return 0


if __name__ == "__main__":
    sys.exit(main())
