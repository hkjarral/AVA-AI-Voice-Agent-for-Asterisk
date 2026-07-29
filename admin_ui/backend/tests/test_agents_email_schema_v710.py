"""H1: per-agent email schema columns on agents.db.

Fresh DBs must declare email_recipient / email_from / email_enabled, and
existing pre-7.1.0 DBs (without these columns) must gain them via the
additive PRAGMA-table_info-guarded migration on open.
"""
import sqlite3

from agents_store import AgentsStore

EMAIL_COLUMNS = {"email_recipient", "email_from", "email_enabled"}


def _columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {str(r[1]) for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
    finally:
        conn.close()


def test_email_columns_exist_and_default(tmp_path):
    adb = str(tmp_path / "agents.db")
    store = AgentsStore(db_path=adb)
    store.close()
    assert EMAIL_COLUMNS <= _columns(adb)


def test_existing_db_gets_columns_added(tmp_path):
    adb = str(tmp_path / "agents.db")
    # Old-shape agents table lacking the email columns.
    conn = sqlite3.connect(adb)
    conn.executescript(
        """
        CREATE TABLE agents (
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            provider TEXT NOT NULL,
            prompt TEXT NOT NULL,
            is_operator_managed INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    assert not (EMAIL_COLUMNS <= _columns(adb))

    store = AgentsStore(db_path=adb)
    store.close()
    assert EMAIL_COLUMNS <= _columns(adb)
