# -*- coding: utf-8 -*-
"""
store.py — Persistência do brand book em SQLite (stdlib).

Guarda o brand book inteiro (nós + métricas + estado do motor) como um JSON
versionado por marca. Simples e suficiente para o v1; dá pra evoluir depois.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).with_name("ponto_zero.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Cria as tabelas se não existirem. Chamado no boot do servidor."""
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS brandbook (
                brand_id   TEXT PRIMARY KEY,
                payload    TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Guarda de token OAuth do Instagram (Graph API). Dormente até haver
        # credenciais Meta + App Review. NUNCA guardamos senha — só o token
        # emitido pelo OAuth, e ele vive só no servidor.
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS ig_token (
                session      TEXT PRIMARY KEY,
                access_token TEXT NOT NULL,
                ig_user_id   TEXT,
                expires_at   TEXT,
                updated_at   TEXT NOT NULL
            )
            """
        )


def save_ig_token(session: str, access_token: str, ig_user_id: str | None,
                  expires_at: str | None, now_iso: str) -> None:
    """Upsert do token do Instagram para uma sessão."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO ig_token (session, access_token, ig_user_id, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session) DO UPDATE SET
                access_token=excluded.access_token, ig_user_id=excluded.ig_user_id,
                expires_at=excluded.expires_at, updated_at=excluded.updated_at
            """,
            (session, access_token, ig_user_id, expires_at, now_iso),
        )


def load_ig_token(session: str) -> dict[str, Any] | None:
    """Recupera o token guardado para a sessão (ou None)."""
    with _conn() as c:
        row = c.execute(
            "SELECT access_token, ig_user_id, expires_at FROM ig_token WHERE session=?",
            (session,),
        ).fetchone()
    if row is None:
        return None
    return {"access_token": row["access_token"], "ig_user_id": row["ig_user_id"],
            "expires_at": row["expires_at"]}


def save_brandbook(brand_id: str, payload: dict[str, Any], now_iso: str) -> None:
    """Upsert do brand book. `now_iso` vem do chamador (sem relógio aqui)."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO brandbook (brand_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(brand_id) DO UPDATE SET
                payload=excluded.payload, updated_at=excluded.updated_at
            """,
            (brand_id, json.dumps(payload, ensure_ascii=False), now_iso),
        )


def load_brandbook(brand_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT payload, updated_at FROM brandbook WHERE brand_id=?",
            (brand_id,),
        ).fetchone()
    if row is None:
        return None
    data = json.loads(row["payload"])
    data["_updated_at"] = row["updated_at"]
    return data


def list_brands() -> list[dict[str, str]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT brand_id, updated_at FROM brandbook ORDER BY updated_at DESC"
        ).fetchall()
    return [{"brand_id": r["brand_id"], "updated_at": r["updated_at"]} for r in rows]
