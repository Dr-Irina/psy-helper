"""Эмбеддинги для clean_segments через intfloat/multilingual-e5-large.

Contextual: passage включает title + summary (готовый контекст от сегментации) +
имя лекции, а не только голый текст — это ситуирует блок и снижает промахи поиска
(идея Contextual RAG, но контекст у нас уже в title/summary).

E5-семейство требует префиксы: "passage: <text>" для документов, "query: <text>"
для запросов. 1024-dim, мультиязычная.

Идемпотентно: пропускает segment_id с уже посчитанным embedding.
  --reembed  — переэмбеддить ВСЕ (очистить segment_embeddings) после смены формулы.

Запуск:
    docker compose run --rm app python scripts/embed_segments.py [--reembed]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from psy_helper.db.connection import connect

MODEL_NAME = "intfloat/multilingual-e5-large"
PASSAGE_PREFIX = "passage: "


def _lecture_name(source_file: str | None) -> str:
    return os.path.basename(os.path.dirname(source_file)) if source_file else ""


def _contextual_passage(title, summary, text, source_file) -> str:
    """passage: [Лекция: X] Заголовок. Резюме. Текст — контекст + содержание."""
    lec = _lecture_name(source_file)
    parts = []
    if lec:
        parts.append(f"[Лекция: {lec}]")
    if title:
        parts.append(f"{title}.")
    if summary:
        parts.append(summary)
    parts.append((text or "").strip())
    return PASSAGE_PREFIX + " ".join(p for p in parts if p).strip()


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reembed", action="store_true", help="очистить и переэмбеддить все")
    args = ap.parse_args()

    with connect() as conn:
        register_vector(conn)
        if args.reembed:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE segment_embeddings")
            conn.commit()
            print("segment_embeddings очищена — переэмбеддим все.")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cs.id, cs.title, cs.summary, cs.text, rt.source_file
                FROM clean_segments cs
                JOIN raw_transcripts rt ON rt.id = cs.raw_id
                LEFT JOIN segment_embeddings se ON se.segment_id = cs.id
                WHERE se.segment_id IS NULL
                ORDER BY cs.id
                """
            )
            rows = cur.fetchall()

        if not rows:
            print("Нечего эмбеддить — все сегменты уже посчитаны.")
            return 0

        print(f"Сегментов к обработке: {len(rows)}")
        print(f"Загрузка модели {MODEL_NAME} (на первом запуске качается ~2.2 ГБ)...")
        t0 = time.monotonic()
        model = SentenceTransformer(MODEL_NAME)
        print(f"Модель загружена за {time.monotonic() - t0:.1f}с")

        ids = [r[0] for r in rows]
        texts = [_contextual_passage(r[1], r[2], r[3], r[4]) for r in rows]

        t0 = time.monotonic()
        embeddings = model.encode(
            texts,
            batch_size=8,
            show_progress_bar=True,
            normalize_embeddings=True,
        )
        print(f"Энкодинг {len(texts)} сегментов за {time.monotonic() - t0:.1f}с")

        with conn.cursor() as cur:
            for seg_id, emb in zip(ids, embeddings):
                cur.execute(
                    "INSERT INTO segment_embeddings (segment_id, embedding) VALUES (%s, %s)",
                    (seg_id, emb),
                )
        conn.commit()
        print(f"Записано {len(rows)} эмбеддингов в segment_embeddings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
