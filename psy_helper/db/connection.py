"""Подключение к Postgres. Берёт DATABASE_URL или собирает из POSTGRES_* env."""
from __future__ import annotations

import os

import psycopg


def database_url() -> str:
    if url := os.getenv("DATABASE_URL"):
        return url
    user = os.getenv("POSTGRES_USER", "psy")
    password = os.getenv("POSTGRES_PASSWORD", "psy")
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "psy_helper")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url(), autocommit=False)
