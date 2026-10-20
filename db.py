"""Banco de dados local (SQLite): termos, auditoria, cache e checklist de inventário."""
import json
import sqlite3
import time
from contextlib import closing

SCHEMA = """
CREATE TABLE IF NOT EXISTS consents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id INTEGER NOT NULL,
    client_name TEXT NOT NULL,
    purpose TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    operator_id INTEGER NOT NULL,
    operator_name TEXT,
    action TEXT NOT NULL,
    target TEXT,
    consent_id INTEGER,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terms (
    operator_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    accepted_at INTEGER NOT NULL,
    PRIMARY KEY (operator_id, version)
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
-- Novo: checklist de inventário (status e nota por item). CREATE IF NOT EXISTS não mexe no que já existe.
CREATE TABLE IF NOT EXISTS inventario (
    operator_id INTEGER NOT NULL,
    item INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pendente',
    nota TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (operator_id, item)
);
"""


class Database:
    def __init__(self, path: str = "bot.db"):
        self.path = path
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------- termos de uso ----------
    def accept_terms(self, operator_id: int, version: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO terms (operator_id, version, accepted_at) VALUES (?, ?, ?)",
                (operator_id, version, int(time.time())),
            )

    def has_accepted_terms(self, operator_id: int, version: int) -> bool:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM terms WHERE operator_id = ? AND version = ?",
                (operator_id, version),
            ).fetchone()
        return row is not None

    # ---------- autorizações (módulo antigo, não usado na versão atual) ----------
    def create_consent(self, operator_id: int, client_name: str, purpose: str, days: int = 7) -> int:
        now = int(time.time())
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "INSERT INTO consents (operator_id, client_name, purpose, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (operator_id, client_name, purpose, now, now + days * 86400),
            )
            return cur.lastrowid

    def get_valid_consent(self, consent_id: int, operator_id: int):
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM consents WHERE id = ? AND operator_id = ? "
                "AND revoked = 0 AND expires_at > ?",
                (consent_id, operator_id, int(time.time())),
            ).fetchone()

    def revoke_consent(self, consent_id: int, operator_id: int) -> bool:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "UPDATE consents SET revoked = 1 WHERE id = ? AND operator_id = ? AND revoked = 0",
                (consent_id, operator_id),
            )
            return cur.rowcount > 0

    def list_active_consents(self, operator_id: int, limit: int = 20):
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM consents WHERE operator_id = ? AND revoked = 0 AND expires_at > ? "
                "ORDER BY id DESC LIMIT ?",
                (operator_id, int(time.time()), limit),
            ).fetchall()

    # ---------- auditoria ----------
    def log(self, operator_id: int, operator_name: str, action: str,
            target: str | None, consent_id: int | None, status: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO audit (ts, operator_id, operator_name, action, target, consent_id, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(time.time()), operator_id, operator_name, action, target, consent_id, status),
            )

    def recent_audit(self, operator_id: int, limit: int = 10):
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM audit WHERE operator_id = ? ORDER BY id DESC LIMIT ?",
                (operator_id, limit),
            ).fetchall()

    def purge_audit(self, days: int) -> int:
        """Apaga registros de auditoria mais antigos que `days` dias. Devolve quantos apagou."""
        limite = int(time.time()) - days * 86400
        with closing(self._connect()) as conn, conn:
            cur = conn.execute("DELETE FROM audit WHERE ts < ?", (limite,))
            return cur.rowcount

    # ---------- cache ----------
    def cache_get(self, key: str):
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT value FROM cache WHERE key = ? AND expires_at > ?",
                (key, int(time.time())),
            ).fetchone()
        return json.loads(row["value"]) if row else None

    def cache_set(self, key: str, value: dict, ttl_seconds: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), int(time.time()) + ttl_seconds),
            )

    # ---------- checklist de inventário ----------
    def inv_get(self, operator_id: int) -> dict:
        """Devolve {item: (status, nota)} só dos itens que já foram mexidos."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT item, status, nota FROM inventario WHERE operator_id = ?",
                (operator_id,),
            ).fetchall()
        return {r["item"]: (r["status"], r["nota"]) for r in rows}

    def inv_set_status(self, operator_id: int, item: int, status: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO inventario (operator_id, item, status, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(operator_id, item) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
                (operator_id, item, status, int(time.time())),
            )

    def inv_set_note(self, operator_id: int, item: int, nota: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO inventario (operator_id, item, nota, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(operator_id, item) DO UPDATE SET nota = excluded.nota, updated_at = excluded.updated_at",
                (operator_id, item, nota, int(time.time())),
            )
