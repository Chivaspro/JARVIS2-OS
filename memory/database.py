"""Brain memory database layer.

SQLite-backed persistent source of truth for the JARVIS brain memory system.

Design notes
------------
* One memory table with rich metadata (the spec's exact field set), plus
  purpose-built tables for tasks, experiences, workflows, corrections,
  tool/provider statistics and relationships.
* All writes go through transactions. One bad record can never poison the
  whole store - callers that pass invalid data get caught and logged.
* The existing JSON memory store (memory/memory_manager.py) is intentionally
  left untouched: this module is an additive layer, not a replacement.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable


class BrainDatabaseError(RuntimeError):
    """Raised on schema/write failures that should surface to the caller."""


_frozen = getattr(__import__("sys"), "frozen", False)


def _base_dir() -> Path:
    if _frozen:
        return Path(__import__("sys").executable).parent
    return Path(__file__).resolve().parent.parent


DEFAULT_DB_PATH = _base_dir() / "data" / "brain_memory.db"


def _ensure_writable(path: Path) -> None:
    if path.suffix:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path.mkdir(parents=True, exist_ok=True)


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    source          TEXT    DEFAULT '',
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    importance      REAL    DEFAULT 0.5,
    confidence      REAL    DEFAULT 0.6,
    relevance       REAL    DEFAULT 0.0,
    access_count    INTEGER DEFAULT 0,
    last_accessed   REAL    DEFAULT 0,
    project_id      TEXT    DEFAULT '',
    tags            TEXT    DEFAULT '[]',
    status          TEXT    DEFAULT 'active',
    superseded_by   INTEGER DEFAULT 0,
    expiration      REAL    DEFAULT 0,
    privacy_level   TEXT    DEFAULT 'normal',
    embedding       BLOB    DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_cat   ON memories(category);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_mem_upd   ON memories(updated_at);

CREATE TABLE IF NOT EXISTS relationships (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id     INTEGER NOT NULL,
    to_id       INTEGER NOT NULL,
    rel_type    TEXT    NOT NULL,
    weight      REAL    DEFAULT 1.0,
    created_at  REAL    NOT NULL,
    UNIQUE(from_id, to_id, rel_type)
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    status          TEXT    DEFAULT 'active',
    project_id      TEXT    DEFAULT '',
    steps_done      TEXT    DEFAULT '[]',
    steps_remaining TEXT    DEFAULT '[]',
    errors          TEXT    DEFAULT '[]',
    solutions       TEXT    DEFAULT '[]',
    files           TEXT    DEFAULT '[]',
    decisions       TEXT    DEFAULT '[]',
    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    memory_id       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS experiences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    summary     TEXT    NOT NULL,
    kind        TEXT    DEFAULT 'experience',
    outcome     TEXT    DEFAULT 'success',
    context     TEXT    DEFAULT '',
    links       TEXT    DEFAULT '[]',
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS workflows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_key     TEXT    NOT NULL,
    description    TEXT    DEFAULT '',
    steps          TEXT    NOT NULL,
    success_count  INTEGER DEFAULT 1,
    fail_count     INTEGER DEFAULT 0,
    last_used      REAL    DEFAULT 0,
    created_at     REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_intent ON workflows(intent_key);

CREATE TABLE IF NOT EXISTS corrections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    original     TEXT    NOT NULL,
    corrected    TEXT    NOT NULL,
    category     TEXT    DEFAULT '',
    context      TEXT    DEFAULT '',
    confidence   REAL    DEFAULT 0.9,
    created_at   REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_stats (
    tool_name    TEXT PRIMARY KEY,
    intent_key   TEXT DEFAULT '',
    success      INTEGER DEFAULT 0,
    failed       INTEGER DEFAULT 0,
    total_ms     REAL    DEFAULT 0,
    updated_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_intent ON tool_stats(intent_key, tool_name);

CREATE TABLE IF NOT EXISTS provider_stats (
    provider_name TEXT PRIMARY KEY,
    success       INTEGER DEFAULT 0,
    failed        INTEGER DEFAULT 0,
    total_ms      REAL    DEFAULT 0,
    updated_at    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at   REAL DEFAULT 0,
    summary    TEXT DEFAULT ''
);
"""


class BrainDatabase:
    """Thin, safe SQLite facade with transactions and migrations."""

    def __init__(self, path: str | os.PathLike | None = None):
        self._path = Path(path) if path else DEFAULT_DB_PATH
        _ensure_writable(self._path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _migrate(self) -> None:
        with self._lock:
            try:
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
            except sqlite3.Error as exc:
                raise BrainDatabaseError(f"Schema migration failed: {exc}")

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # ── helpers ───────────────────────────────────────────────────────────────

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            try:
                cur = self._conn.execute(sql, tuple(params))
                self._conn.commit()
                return cur
            except sqlite3.Error as exc:
                raise BrainDatabaseError(f"SQL failed: {exc} | {sql[:80]}")

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                cur = self._conn.execute(sql, tuple(params))
                return cur.fetchall()
            except sqlite3.Error as exc:
                raise BrainDatabaseError(f"SQL failed: {exc} | {sql[:80]}")

    # ── memories (CRUD) ───────────────────────────────────────────────────────

    def insert_memory(self, m: dict) -> int:
        now = time.time()
        cur = self._execute(
            """INSERT INTO memories
               (category, content, source, created_at, updated_at, importance,
                confidence, relevance, access_count, last_accessed, project_id,
                tags, status, superseded_by, expiration, privacy_level, embedding)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                m.get("category"), m.get("content"), m.get("source", ""),
                m.get("created_at", now), now,
                float(m.get("importance", 0.5)), float(m.get("confidence", 0.6)),
                float(m.get("relevance", 0.0)), int(m.get("access_count", 0)),
                m.get("last_accessed", 0), m.get("project_id", ""),
                json.dumps(m.get("tags", [])), m.get("status", "active"),
                int(m.get("superseded_by", 0)), float(m.get("expiration", 0)),
                m.get("privacy_level", "normal"), m.get("embedding"),
            ),
        )
        return cur.lastrowid

    def update_memory(self, mid: int, fields: dict) -> None:
        if not fields:
            return
        fields = dict(fields)
        fields["updated_at"] = time.time()
        if "tags" in fields:
            fields["tags"] = json.dumps(fields["tags"] or [])
        cols = ", ".join(f"{k}=?" for k in fields)
        self._execute(f"UPDATE memories SET {cols} WHERE id=?", (*fields.values(), mid))

    def get_memory(self, mid: int) -> dict | None:
        rows = self._query("SELECT * FROM memories WHERE id=?", (mid,))
        return self._row_to_memory(rows[0]) if rows else None

    def touch_memory(self, mid: int) -> None:
        self._execute(
            "UPDATE memories SET access_count=access_count+1, last_accessed=? WHERE id=?",
            (time.time(), mid),
        )

    def delete_memory(self, mid: int) -> None:
        self._execute("DELETE FROM memories WHERE id=?", (mid,))

    def list_memories(
        self,
        category: str = "",
        status: str = "active",
        project_id: str = "",
        limit: int = 500,
    ) -> list[dict]:
        sql = "SELECT * FROM memories WHERE 1=1"
        args: list[Any] = []
        if category:
            sql += " AND category=?"; args.append(category)
        if status:
            sql += " AND status=?"; args.append(status)
        if project_id:
            sql += " AND project_id=?"; args.append(project_id)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(int(limit))
        return [self._row_to_memory(r) for r in self._query(sql, args)]

    def iterate_active(self) -> Iterable[dict]:
        rows = self._query(
            "SELECT * FROM memories WHERE status='active' ORDER BY updated_at DESC"
        )
        for r in rows:
            yield self._row_to_memory(r)

    def mark_superseded(self, old_id: int, new_id: int) -> None:
        self._execute(
            "UPDATE memories SET status='superseded', superseded_by=? WHERE id=?",
            (new_id, old_id),
        )

    def mark_deleted(self, mid: int) -> None:
        self._execute("UPDATE memories SET status='deleted', updated_at=? WHERE id=?",
                      (time.time(), mid))

    def count(self) -> dict:
        rows = self._query(
            "SELECT category, COUNT(*) AS n FROM memories WHERE status='active' GROUP BY category"
        )
        return {r["category"]: r["n"] for r in rows} if rows else {}

    @staticmethod
    def _row_to_memory(r: sqlite3.Row) -> dict:
        def _tags(raw):
            try:
                return json.loads(raw) if raw else []
            except Exception:
                return []
        return {
            "id": r["id"], "category": r["category"], "content": r["content"],
            "source": r["source"], "created_at": r["created_at"],
            "updated_at": r["updated_at"], "importance": r["importance"],
            "confidence": r["confidence"], "relevance": r["relevance"],
            "access_count": r["access_count"], "last_accessed": r["last_accessed"],
            "project_id": r["project_id"], "tags": _tags(r["tags"]),
            "status": r["status"], "superseded_by": r["superseded_by"],
            "expiration": r["expiration"], "privacy_level": r["privacy_level"],
            "embedding": r["embedding"],
        }

    # ── relationships / graph ─────────────────────────────────────────────────

    def add_relationship(self, from_id: int, to_id: int, rel_type: str, weight: float = 1.0) -> None:
        now = time.time()
        self._execute(
            """INSERT OR IGNORE INTO relationships(from_id,to_id,rel_type,weight,created_at)
               VALUES (?,?,?,?,?)""",
            (from_id, to_id, rel_type, float(weight), now),
        )

    def set_relationship_weight(self, from_id: int, to_id: int, rel_type: str, weight: float) -> None:
        self._execute(
            "UPDATE relationships SET weight=? WHERE from_id=? AND to_id=? AND rel_type=?",
            (float(weight), from_id, to_id, rel_type),
        )

    def relationships_of(self, mid: int, rel_types: Iterable[str] | None = None) -> list[dict]:
        sql = """SELECT from_id AS a, to_id AS b, rel_type AS t, weight AS w
                 FROM relationships WHERE from_id=? OR to_id=?"""
        args: list[Any] = [mid, mid]
        rels: list[dict] = []
        for r in self._query(sql, args):
            if rel_types and r["t"] not in rel_types:
                continue
            other = r["b"] if r["a"] == mid else r["a"]
            rels.append({"other": other, "type": r["t"], "weight": r["w"]})
        return rels

    # ── tasks ─────────────────────────────────────────────────────────────────

    def insert_task(self, t: dict) -> int:
        now = time.time()
        cur = self._execute(
            """INSERT INTO tasks(title,status,project_id,steps_done,steps_remaining,
               errors,solutions,files,decisions,created_at,updated_at,memory_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t.get("title"), t.get("status", "active"), t.get("project_id", ""),
                json.dumps(t.get("steps_done", [])), json.dumps(t.get("steps_remaining", [])),
                json.dumps(t.get("errors", [])), json.dumps(t.get("solutions", [])),
                json.dumps(t.get("files", [])), json.dumps(t.get("decisions", [])),
                now, now, int(t.get("memory_id", 0)),
            ),
        )
        return cur.lastrowid

    def update_task(self, tid: int, fields: dict) -> None:
        fields = dict(fields)
        fields["updated_at"] = time.time()
        for k in ("steps_done", "steps_remaining", "errors", "solutions", "files", "decisions"):
            if k in fields:
                fields[k] = json.dumps(fields[k] or [])
        cols = ", ".join(f"{k}=?" for k in fields)
        self._execute(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), tid))

    def get_task(self, tid: int) -> dict | None:
        rows = self._query("SELECT * FROM tasks WHERE id=?", (tid,))
        return self._row_to_task(rows[0]) if rows else None

    def list_tasks(self, status: str = "active", project_id: str = "", limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        args: list[Any] = []
        if status:
            sql += " AND status=?"; args.append(status)
        if project_id:
            sql += " AND project_id=?"; args.append(project_id)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(int(limit))
        return [self._row_to_task(r) for r in self._query(sql, args)]

    @staticmethod
    def _row_to_task(r: sqlite3.Row) -> dict:
        def _j(raw):
            try:
                return json.loads(raw) if raw else []
            except Exception:
                return []
        return {
            "id": r["id"], "title": r["title"], "status": r["status"],
            "project_id": r["project_id"], "steps_done": _j(r["steps_done"]),
            "steps_remaining": _j(r["steps_remaining"]), "errors": _j(r["errors"]),
            "solutions": _j(r["solutions"]), "files": _j(r["files"]),
            "decisions": _j(r["decisions"]), "created_at": r["created_at"],
            "updated_at": r["updated_at"], "memory_id": r["memory_id"],
        }

    # ── experiences / workflows ───────────────────────────────────────────────

    def insert_experience(self, e: dict) -> int:
        cur = self._execute(
            """INSERT INTO experiences(summary,kind,outcome,context,links,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                e.get("summary"), e.get("kind", "experience"),
                e.get("outcome", "success"), e.get("context", ""),
                json.dumps(e.get("links", [])), time.time(),
            ),
        )
        return cur.lastrowid

    def list_experiences(self, kind: str = "", outcome: str = "", limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM experiences WHERE 1=1"
        args: list[Any] = []
        if kind:
            sql += " AND kind=?"; args.append(kind)
        if outcome:
            sql += " AND outcome=?"; args.append(outcome)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(int(limit))
        out = []
        for r in self._query(sql, args):
            try:
                links = json.loads(r["links"]) if r["links"] else []
            except Exception:
                links = []
            out.append({"id": r["id"], "summary": r["summary"], "kind": r["kind"],
                        "outcome": r["outcome"], "context": r["context"],
                        "links": links, "created_at": r["created_at"]})
        return out

    def upsert_workflow(self, w: dict) -> None:
        now = time.time()
        rows = self._query(
            "SELECT id, success_count, fail_count FROM workflows WHERE intent_key=?",
            (w.get("intent_key", ""),),
        )
        if rows:
            rid, sc, fc = rows[0]["id"], rows[0]["success_count"], rows[0]["fail_count"]
            w_succ = int(w.get("success_count", 0))
            w_fail = int(w.get("fail_count", 0))
            self._execute(
                """UPDATE workflows SET steps=?, success_count=?, fail_count=?,
                   last_used=?, description=? WHERE id=?""",
                (
                    json.dumps(w.get("steps", [])), sc + w_succ, fc + w_fail,
                    now, w.get("description", ""), rid,
                ),
            )
        else:
            self._execute(
                """INSERT INTO workflows(intent_key,description,steps,success_count,
                   fail_count,last_used,created_at) VALUES (?,?,?,?,?,?,?)""",
                (
                    w.get("intent_key", ""), w.get("description", ""),
                    json.dumps(w.get("steps", [])), int(w.get("success_count", 1)),
                    int(w.get("fail_count", 0)), now, now,
                ),
            )

    def best_workflow(self, intent_key: str) -> dict | None:
        rows = self._query(
            """SELECT * FROM workflows WHERE intent_key=?
               ORDER BY (success_count - fail_count) DESC, last_used DESC LIMIT 1""",
            (intent_key,),
        )
        if not rows:
            return None
        r = rows[0]
        return {"id": r["id"], "intent_key": r["intent_key"], "description": r["description"],
                "steps": r["steps"], "success_count": r["success_count"],
                "fail_count": r["fail_count"], "last_used": r["last_used"]}

    def list_workflows(self, limit: int = 100) -> list[dict]:
        rows = self._query(
            "SELECT * FROM workflows ORDER BY (success_count - fail_count) DESC, last_used DESC LIMIT ?",
            (int(limit),),
        )
        out = []
        for r in rows:
            try:
                steps = json.loads(r["steps"]) if r["steps"] else []
            except Exception:
                steps = []
            out.append({"id": r["id"], "intent_key": r["intent_key"],
                        "description": r["description"], "steps": steps,
                        "success_count": r["success_count"], "fail_count": r["fail_count"],
                        "last_used": r["last_used"]})
        return out

    # ── corrections ───────────────────────────────────────────────────────────

    def insert_correction(self, c: dict) -> int:
        cur = self._execute(
            """INSERT INTO corrections(original,corrected,category,context,confidence,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                c.get("original", ""), c.get("corrected", ""),
                c.get("category", ""), c.get("context", ""),
                float(c.get("confidence", 0.9)), time.time(),
            ),
        )
        return cur.lastrowid

    def list_corrections(self, limit: int = 50) -> list[dict]:
        rows = self._query("SELECT * FROM corrections ORDER BY created_at DESC LIMIT ?", (int(limit),))
        return [dict(r) for r in rows]

    # ── tool & provider stats ─────────────────────────────────────────────────

    def record_tool_result(self, tool: str, ok: bool, ms: float, intent: str = "") -> None:
        now = time.time()
        held = self._query(
            "SELECT tool_name FROM tool_stats WHERE tool_name=? AND intent_key=?",
            (tool, intent),
        )
        if held:
            col = "success" if ok else "failed"
            self._execute(
                f"UPDATE tool_stats SET {col}={col}+1, total_ms=total_ms+?, updated_at=? "
                "WHERE tool_name=? AND intent_key=?",
                (ms, now, tool, intent),
            )
        else:
            self._execute(
                """INSERT INTO tool_stats(tool_name,intent_key,success,failed,total_ms,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (tool, intent, 1 if ok else 0, 0 if ok else 1, ms, now),
            )

    def tool_stats(self, intent: str = "") -> list[dict]:
        sql = "SELECT * FROM tool_stats WHERE 1=1"
        args: list[Any] = []
        if intent:
            sql += " AND intent_key=?"; args.append(intent)
        sql += " ORDER BY success DESC, success+failed DESC"
        return [dict(r) for r in self._query(sql, args)]

    def record_provider_result(self, provider: str, ok: bool, ms: float) -> None:
        now = time.time()
        held = self._query("SELECT provider_name FROM provider_stats WHERE provider_name=?", (provider,))
        if held:
            col = "success" if ok else "failed"
            self._execute(
                f"UPDATE provider_stats SET {col}={col}+1, total_ms=total_ms+?, updated_at=? "
                "WHERE provider_name=?",
                (ms, now, provider),
            )
        else:
            self._execute(
                """INSERT INTO provider_stats(provider_name,success,failed,total_ms,updated_at)
                   VALUES (?,?,?,?,?)""",
                (provider, 1 if ok else 0, 0 if ok else 1, ms, now),
            )

    def provider_stats(self) -> list[dict]:
        rows = self._query("SELECT * FROM provider_stats ORDER BY success DESC")
        return [dict(r) for r in rows]

    # ── kv / sessions ─────────────────────────────────────────────────────────

    def kv_get(self, key: str, default: Any = None) -> Any:
        rows = self._query("SELECT value FROM kv WHERE key=?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except Exception:
            return rows[0]["value"]

    def kv_set(self, key: str, value: Any) -> None:
        self._execute(
            "INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def start_session(self) -> int:
        cur = self._execute("INSERT INTO sessions(started_at) VALUES (?)", (time.time(),))
        return cur.lastrowid

    def close_session(self, sid: int, summary: str = "") -> None:
        self._execute(
            "UPDATE sessions SET ended_at=?, summary=? WHERE id=?",
            (time.time(), summary, sid),
        )

    def recent_sessions(self, limit: int = 10) -> list[dict]:
        rows = self._query("SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (int(limit),))
        return [dict(r) for r in rows]

    # ── maintenance ───────────────────────────────────────────────────────────

    def backup(self, dest: str | os.PathLike | None = None) -> Path:
        if dest is None:
            dest = self._path.with_suffix(f".backup_{int(time.time())}.db")
        else:
            dest = Path(dest)
        _ensure_writable(dest)
        with self._lock:
            src_conn = self._conn if self._path.suffix else None
            try:
                backup = sqlite3.connect(str(dest))
                self._conn.backup(backup)
                backup.close()
            except sqlite3.Error as exc:
                raise BrainDatabaseError(f"Backup failed: {exc}")
        return Path(dest)

    def export_json(self, dest: str | os.PathLike | None = None) -> Path:
        if dest is None:
            dest = self._path.with_suffix(f".export_{int(time.time())}.json")
        data = {
            "memories": self.list_memories(status="", limit=10000),
            "relationships": [dict(r) for r in self._query("SELECT * FROM relationships")],
            "tasks": self.list_tasks(status="", limit=10000),
            "experiences": self.list_experiences(limit=10000),
            "workflows": self.list_workflows(limit=10000),
            "tool_stats": self.tool_stats(),
            "provider_stats": self.provider_stats(),
        }
        with self._lock:
            Path(dest).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                                  encoding="utf-8")
        return Path(dest)