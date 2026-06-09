"""Семантическая консолидация дублей концептов (по эмбеддингам).

После ingest_concepts_v2.py + embed_concepts.py концепты с РАЗНЫМИ именами, но
одинаковым смыслом (напр. «5 ресурсов» и «5 видов ресурсов человека») остаются
отдельными строками. Здесь они объединяются:
  - кандидаты в дубли = пары одного type с косинусной близостью ≥ порога
    (поиск ближайших через HNSW-индекс pgvector);
  - кластеры через union-find;
  - канонический = с максимальной salience (тай-брейк: больше всего цитат/длиннее
    description); в него сливаются quotes (union по тексту), source_segments,
    topics/hunt_stages (union), salience = max; остальные строки удаляются.

По умолчанию DRY-RUN: только показывает кластеры. Применить: --apply.

Запуск (в docker, после embed_concepts.py):
    docker compose run --rm app python scripts/consolidate_concepts.py            # dry-run
    docker compose run --rm app python scripts/consolidate_concepts.py --apply
    docker compose run --rm app python scripts/consolidate_concepts.py --sim 0.95 # порог
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv
from pgvector.psycopg import register_vector

from psy_helper.db.connection import connect

DEFAULT_SIM = 0.93   # косинусная близость; dist = 1 - sim
NEIGHBORS = 25       # сколько ближайших проверять на концепт


class UF:
    def __init__(self, ids):
        self.p = {i: i for i in ids}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def build_clusters(cur, sim: float) -> list[list[str]]:
    max_dist = 1.0 - sim
    cur.execute("SELECT id, type FROM concepts WHERE embedding IS NOT NULL")
    rows = cur.fetchall()
    ids = [r[0] for r in rows]
    uf = UF(ids)
    for cid, ctype in rows:
        cur.execute(
            """
            SELECT c2.id
            FROM concepts c1
            JOIN concepts c2
              ON c2.type = c1.type AND c2.id <> c1.id AND c2.embedding IS NOT NULL
             AND (c1.embedding <=> c2.embedding) < %s
            WHERE c1.id = %s
            ORDER BY (c1.embedding <=> c2.embedding)
            LIMIT %s
            """,
            (max_dist, cid, NEIGHBORS),
        )
        for (nid,) in cur.fetchall():
            uf.union(cid, nid)
    clusters: dict[str, list[str]] = {}
    for i in ids:
        clusters.setdefault(uf.find(i), []).append(i)
    return [c for c in clusters.values() if len(c) > 1]


def merge_cluster(cur, ids: list[str]) -> None:
    cur.execute(
        """
        SELECT id, name, type, description, salience,
               COALESCE(source_segments, '{}') AS segs,
               COALESCE(quotes, '[]'::jsonb) AS quotes,
               COALESCE(topics, '{}') AS topics,
               COALESCE(hunt_stages, '{}') AS hunt
        FROM concepts WHERE id = ANY(%s)
        """,
        (ids,),
    )
    rows = cur.fetchall()
    # канонический: max salience, потом больше цитат, потом длиннее description
    def score(r):
        return (r[4] or 0, len(r[6] or []), len(r[3] or ""))
    rows.sort(key=score, reverse=True)
    canon = rows[0]
    canon_id = canon[0]

    seen_q, quotes = set(), []
    segs, topics, hunt = set(), set(), set()
    salience = 0
    for r in rows:
        salience = max(salience, r[4] or 0)
        segs.update(r[5] or [])
        topics.update(r[7] or [])
        hunt.update(r[8] or [])
        for q in (r[6] or []):
            key = (q.get("text") or "").strip().lower()
            if key and key not in seen_q:
                seen_q.add(key)
                quotes.append(q)

    from psycopg.types.json import Json
    cur.execute(
        """
        UPDATE concepts SET
          quotes = %s, salience = %s,
          source_segments = %s,
          topics = %s, hunt_stages = %s
        WHERE id = %s
        """,
        (Json(quotes), salience, list(segs),
         list(topics) or None, list(hunt) or None, canon_id),
    )
    others = [r[0] for r in rows[1:]]
    cur.execute("DELETE FROM concepts WHERE id = ANY(%s)", (others,))


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="реально мерджить (иначе dry-run)")
    ap.add_argument("--sim", type=float, default=DEFAULT_SIM, help="порог косинусной близости")
    args = ap.parse_args()

    with connect() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            clusters = build_clusters(cur, args.sim)
            dup_rows = sum(len(c) for c in clusters)
            print(f"Кластеров-дублей: {len(clusters)} | строк в них: {dup_rows} "
                  f"| схлопнётся в: {len(clusters)} (минус {dup_rows - len(clusters)})")
            # показать примеры
            for cl in clusters[:25]:
                cur.execute("SELECT name, type, salience FROM concepts WHERE id = ANY(%s)", (cl,))
                names = cur.fetchall()
                t = names[0][1]
                print(f"  [{t}] " + "  |  ".join(f"{n}·s{s}" for n, _t, s in names))
            if len(clusters) > 25:
                print(f"  … ещё {len(clusters) - 25} кластеров")

            if not args.apply:
                print("\nDRY-RUN. Для применения: --apply (можно сначала покрутить --sim).")
                return 0

            for cl in clusters:
                merge_cluster(cur, cl)
            conn.commit()
            cur.execute("SELECT count(*) FROM concepts")
            print(f"\nГотово. Концептов после консолидации: {cur.fetchone()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
