"""Database connection and table initialization for ASRT data collection.

Supports PostgreSQL via DATABASE_URL and falls back automatically to SQLite.
"""

from __future__ import annotations

import os
import json
import sqlite3
from pathlib import Path
from typing import Any

# The one true path. Anything that reads history (cli.py's /db, /inspect,
# /trace) must resolve the db through this constant, never a relative literal
# -- a relative "results/asrt_history.db" resolves against the caller's cwd,
# not this file, and silently opens (or creates) a different, empty database.
DEFAULT_DB_PATH = Path(__file__).parent / "results" / "asrt_history.db"


class DBManager:
    """Manages database connection, table initialization, and querying."""

    def __init__(self, db_url: str | None = None):
        self.db_url = db_url or os.getenv("DATABASE_URL")
        self.is_postgres = False
        if self.db_url and (self.db_url.startswith("postgresql://") or self.db_url.startswith("postgres://")):
            self.is_postgres = True
        
        self.conn = None
        self._connect()
        self._init_db()

    def _connect(self) -> None:
        if self.is_postgres:
            try:
                import psycopg2
                print("[*] Connecting to PostgreSQL database...")
                self.conn = psycopg2.connect(self.db_url)
                return
            except ImportError:
                print("[!] psycopg2 not installed. PostgreSQL support requires 'psycopg2' or 'psycopg2-binary'.")
            except Exception as e:
                print(f"[!] PostgreSQL connection failed: {e}.")
            
            print("[*] Falling back to SQLite...")
            self.is_postgres = False

        db_path = DEFAULT_DB_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[*] Connecting to SQLite database at {db_path}...")
        self.conn = sqlite3.connect(str(db_path))
        try:
            self.conn.execute("PRAGMA foreign_keys = ON;")
        except Exception:
            pass

    def _init_db(self) -> None:
        """Create runs and results tables if they do not exist."""
        runs_table_sql = """
        CREATE TABLE IF NOT EXISTS runs (
            run_id VARCHAR(100) PRIMARY KEY,
            suite_id VARCHAR(255),
            target_id VARCHAR(100),
            target_version VARCHAR(255),
            judge_id VARCHAR(100),
            judge_version VARCHAR(100),
            timestamp VARCHAR(100),
            elapsed_seconds REAL,
            total_attacks INTEGER,
            attack_success_rate REAL,
            temperature REAL,
            max_tokens INTEGER,
            model_category VARCHAR(100)
        );
        """

        if self.is_postgres:
            results_table_sql = """
            CREATE TABLE IF NOT EXISTS results (
                id SERIAL PRIMARY KEY,
                run_id VARCHAR(100) REFERENCES runs(run_id) ON DELETE CASCADE,
                attack_id VARCHAR(255),
                parent_ids TEXT,
                category VARCHAR(100),
                collection VARCHAR(100),
                intent VARCHAR(100),
                prompt TEXT,
                target_name VARCHAR(255),
                response TEXT,
                verdict VARCHAR(100),
                score REAL,
                judge_confidence REAL,
                detected_intent VARCHAR(100),
                severity VARCHAR(100),
                reasoning TEXT,
                policy_violated VARCHAR(100),
                violated_invariants TEXT,
                metadata TEXT
            );
            """
        else:
            results_table_sql = """
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id VARCHAR(100),
                attack_id VARCHAR(255),
                parent_ids TEXT,
                category VARCHAR(100),
                collection VARCHAR(100),
                intent VARCHAR(100),
                prompt TEXT,
                target_name VARCHAR(255),
                response TEXT,
                verdict VARCHAR(100),
                score REAL,
                judge_confidence REAL,
                detected_intent VARCHAR(100),
                severity VARCHAR(100),
                reasoning TEXT,
                policy_violated VARCHAR(100),
                violated_invariants TEXT,
                metadata TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
            """
        
        self.execute(runs_table_sql)
        self.execute(results_table_sql)
        self._migrate_runs_table()

    def _migrate_runs_table(self) -> None:
        """Safely add new columns to existing runs tables (idempotent)."""
        new_columns = [
            ("temperature", "REAL"),
            ("max_tokens", "INTEGER"),
            ("model_category", "VARCHAR(100)"),
        ]
        for col_name, col_type in new_columns:
            try:
                self.execute(f"ALTER TABLE runs ADD COLUMN {col_name} {col_type}")
            except Exception:
                pass  # Column already exists

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        """Execute a query and commit the transaction."""
        # Convert parameter placeholders from %s to ? for SQLite compatibility
        if not self.is_postgres:
            query = query.replace("%s", "?")
        
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        self.conn.commit()
        return cursor

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[Any]:
        """Fetch all results of a query."""
        if not self.is_postgres:
            query = query.replace("%s", "?")
        
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()

    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None


def get_db(db_url: str | None = None) -> DBManager:
    """Helper function to instantiate and return a DBManager."""
    return DBManager(db_url)
