"""Durable profile-scoped store for Hermes Todo.

Plan, workflow status, and deadline are independent. SQLite remains the single
profile-scoped authority for Desktop, CLI, and optional JSON imports.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from hermes_constants import get_hermes_home as _get_hermes_home
except ImportError:  # Standalone source-tree execution and tests.
    _get_hermes_home = None

VALID_PLANS = frozenset({"now", "today", "later"})
VALID_STATUSES = frozenset({"open", "waiting", "blocked", "done"})
DEFAULT_ESTIMATE = 25
MAX_SOURCE_PAYLOAD_BYTES = 64 * 1024
SCHEMA_VERSION = 3


class BoardError(ValueError):
    """A user-correctable board mutation error."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def resolve_hermes_home() -> Path:
    """Resolve the active profile home, with a standalone source-tree fallback."""
    if _get_hermes_home is not None:
        return Path(_get_hermes_home()).expanduser()
    configured = os.environ.get("HERMES_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".hermes"


def resolve_db_path() -> Path:
    """Return this profile's isolated Hermes Todo database path."""
    return resolve_hermes_home() / "hermes-todo" / "todo.sqlite3"


def _chmod_private_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.chmod(0o600)


def _create_v3_tasks(conn: sqlite3.Connection, table: str = "tasks") -> None:
    conn.execute(
        f"""
        CREATE TABLE {table} (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            plan TEXT NOT NULL CHECK (plan IN ('now', 'today', 'later')),
            status TEXT NOT NULL CHECK (status IN ('open', 'waiting', 'blocked', 'done')),
            estimate INTEGER NOT NULL CHECK (estimate BETWEEN 5 AND 480),
            due_date TEXT,
            due_at TEXT,
            due_timezone TEXT,
            due_language TEXT,
            source TEXT,
            external_id TEXT,
            project TEXT,
            priority INTEGER CHECK (priority IS NULL OR priority BETWEEN 1 AND 4),
            recurrence TEXT,
            source_updated_at TEXT,
            source_payload TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            CHECK (due_date IS NULL OR due_at IS NULL),
            CHECK ((source IS NULL) = (external_id IS NULL))
        )
        """
    )


def _create_v3_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_single_open_now "
        "ON tasks((1)) WHERE plan = 'now' AND status = 'open'"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_source_external "
        "ON tasks(source, external_id) "
        "WHERE source IS NOT NULL AND external_id IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_status_plan_due "
        "ON tasks(status, plan, due_date, due_at, created_at)"
    )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version >= SCHEMA_VERSION:
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
        ).fetchone()
        if not table_exists:
            _create_v3_tasks(conn)
        else:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if {"plan", "status", "due_date", "due_at"}.issubset(columns):
                raise RuntimeError("Unversioned Hermes Todo schema cannot be migrated safely")

            legacy_count = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            _create_v3_tasks(conn, "tasks_v3")
            rows = conn.execute(
                """
                SELECT id, title, lane, estimate, created_at, updated_at, completed_at
                FROM tasks
                ORDER BY created_at, id
                """
            ).fetchall()
            kept_now = False
            for row in rows:
                lane = str(row["lane"])
                plan = "now" if lane == "now" and not kept_now else "today"
                status = {
                    "waiting": "waiting",
                    "done": "done",
                }.get(lane, "open")
                if plan == "now" and status == "open":
                    kept_now = True
                completed_at = row["completed_at"] if status == "done" else None
                conn.execute(
                    """
                    INSERT INTO tasks_v3(
                        id, title, plan, status, estimate,
                        created_at, updated_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"], row["title"], plan, status, row["estimate"],
                        row["created_at"], row["updated_at"], completed_at,
                    ),
                )
            migrated_count = int(conn.execute("SELECT COUNT(*) FROM tasks_v3").fetchone()[0])
            if migrated_count != legacy_count:
                raise RuntimeError("Hermes Todo migration row-count mismatch")
            conn.execute("DROP TABLE tasks")
            conn.execute("ALTER TABLE tasks_v3 RENAME TO tasks")

        _create_v3_indexes(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _connect() -> sqlite3.Connection:
    path = resolve_db_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    _chmod_private_files(path)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS board_meta (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO board_meta(key, value) VALUES ('revision', 0);
        """
    )
    _migrate_schema(conn)
    _chmod_private_files(path)
    return conn


def _clean_title(value: Any) -> str:
    if not isinstance(value, str):
        raise BoardError("Task title must be text")
    title = value.strip()
    if not title:
        raise BoardError("Task title is required")
    if len(title) > 500:
        raise BoardError("Task title must be 500 characters or fewer")
    return title


def _clean_choice(value: Any, choices: frozenset[str], field: str) -> str:
    if not isinstance(value, str):
        raise BoardError(f"{field.capitalize()} must be text")
    cleaned = value.strip().lower()
    if cleaned not in choices:
        raise BoardError(f"Unknown {field}: {cleaned or '(empty)'}")
    return cleaned


def _clean_estimate(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BoardError("Estimate must be a whole number of minutes")
    estimate = value
    if estimate < 5 or estimate > 480:
        raise BoardError("Estimate must be between 5 and 480 minutes")
    return estimate


def _clean_due_date(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BoardError("Due date must be text in YYYY-MM-DD format")
    text = value.strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise BoardError("Due date must be YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise BoardError("Due date must be YYYY-MM-DD")
    return text


def _clean_due_at(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BoardError("Due time must be an ISO datetime string")
    text = value.strip()
    if not text:
        return None
    if "T" not in text and " " not in text:
        raise BoardError("Timed due values need a datetime; use dueDate for all-day dates")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BoardError("Due time must be an ISO datetime") from exc
    return text


def _clean_optional_text(value: Any, field: str, max_length: int = 500) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BoardError(f"{field} must be text")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length:
        raise BoardError(f"{field} must be {max_length} characters or fewer")
    return text


def _clean_priority(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BoardError("Priority must be a whole number from 1 to 4")
    priority = value
    if priority < 1 or priority > 4:
        raise BoardError("Priority must be from 1 to 4")
    return priority


def _clean_timestamp(value: Any, fallback: str) -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, bool):
        raise BoardError("Created time must be an ISO timestamp or integer milliseconds")
    if isinstance(value, int):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
        except (OSError, OverflowError, ValueError) as exc:
            raise BoardError("Created time is outside the supported range") from exc
    if not isinstance(value, str):
        raise BoardError("Created time must be an ISO timestamp or integer milliseconds")
    text = value.strip()
    if not text:
        return fallback
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BoardError("Created time must be an ISO timestamp or integer milliseconds") from exc
    return text


def _clean_source_payload(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise BoardError("Source payload must be valid JSON") from exc
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise BoardError("Source payload must be JSON-compatible") from exc
    if len(text.encode("utf-8")) > MAX_SOURCE_PAYLOAD_BYTES:
        raise BoardError(
            f"Source payload must be {MAX_SOURCE_PAYLOAD_BYTES} UTF-8 bytes or fewer"
        )
    return text


def _legacy_to_dimensions(lane: Any) -> tuple[str, str]:
    if lane is None:
        value = "today"
    elif isinstance(lane, str):
        value = lane.strip().lower()
    else:
        raise BoardError("Lane must be text")
    if value == "now":
        return "now", "open"
    if value in {"today", "later"}:
        return value, "open"
    if value == "waiting":
        return "today", "waiting"
    if value == "blocked":
        return "today", "blocked"
    if value == "done":
        return "today", "done"
    raise BoardError(f"Unknown lane: {value or '(empty)'}")


def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    plan = str(row["plan"])
    status = str(row["status"])
    return {
        "id": row["id"],
        "title": row["title"],
        "lane": status if status != "open" else plan,
        "plan": plan,
        "status": status,
        "estimate": row["estimate"],
        "dueDate": row["due_date"],
        "dueAt": row["due_at"],
        "dueTimezone": row["due_timezone"],
        "dueLanguage": row["due_language"],
        "source": row["source"],
        "externalId": row["external_id"],
        "project": row["project"],
        "priority": row["priority"],
        "recurrence": row["recurrence"],
        "sourceUpdatedAt": row["source_updated_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"],
    }


def _read_board(conn: sqlite3.Connection) -> dict[str, Any]:
    revision_row = conn.execute("SELECT value FROM board_meta WHERE key = 'revision'").fetchone()
    rows = conn.execute(
        """
        SELECT * FROM tasks
        ORDER BY
            CASE status WHEN 'open' THEN 0 WHEN 'waiting' THEN 1 WHEN 'blocked' THEN 2 ELSE 3 END,
            CASE plan WHEN 'now' THEN 0 WHEN 'today' THEN 1 ELSE 2 END,
            COALESCE(due_date, due_at) IS NULL,
            COALESCE(due_date, due_at),
            created_at,
            id
        """
    ).fetchall()
    return {
        "version": 3,
        "revision": int(revision_row["value"] if revision_row else 0),
        "tasks": [_row_to_task(row) for row in rows],
    }


def _bump_revision(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE board_meta SET value = value + 1 WHERE key = 'revision'")


def _demote_other_now(conn: sqlite3.Connection, task_id: str, now: str) -> None:
    conn.execute(
        """
        UPDATE tasks
        SET plan = 'today', updated_at = ?
        WHERE status = 'open' AND plan = 'now' AND id <> ?
        """,
        (now, task_id),
    )


def get_board() -> dict[str, Any]:
    with _connect() as conn:
        return _read_board(conn)


def create_task(
    title: str,
    *,
    estimate: int = DEFAULT_ESTIMATE,
    plan: str | None = None,
    status: str | None = None,
    due_date: str | None = None,
    due_at: str | None = None,
    due_timezone: str | None = None,
    due_language: str | None = None,
    source: str | None = None,
    external_id: str | None = None,
    project: str | None = None,
    priority: int | None = None,
    recurrence: str | None = None,
    source_updated_at: str | None = None,
    source_payload: Any = None,
    lane: str | None = None,
    task_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    legacy_plan, legacy_status = _legacy_to_dimensions(lane)
    clean_plan = _clean_choice(plan if plan is not None else legacy_plan, VALID_PLANS, "plan")
    clean_status = _clean_choice(status if status is not None else legacy_status, VALID_STATUSES, "status")
    clean_due_date = _clean_due_date(due_date)
    clean_due_at = _clean_due_at(due_at)
    if clean_due_date and clean_due_at:
        raise BoardError("A task cannot have both an all-day due date and a timed due value")
    clean_source = _clean_optional_text(source, "Source", 100)
    clean_external = _clean_optional_text(external_id, "External ID", 200)
    if bool(clean_source) != bool(clean_external):
        raise BoardError("Source and external ID must be provided together")

    values = {
        "id": _clean_optional_text(task_id, "Task ID", 200) or uuid.uuid4().hex,
        "title": _clean_title(title),
        "plan": clean_plan,
        "status": clean_status,
        "estimate": _clean_estimate(estimate),
        "due_date": clean_due_date,
        "due_at": clean_due_at,
        "due_timezone": _clean_optional_text(due_timezone, "Due timezone", 100),
        "due_language": _clean_optional_text(due_language, "Due language", 50),
        "source": clean_source,
        "external_id": clean_external,
        "project": _clean_optional_text(project, "Project"),
        "priority": _clean_priority(priority),
        "recurrence": _clean_optional_text(recurrence, "Recurrence"),
        "source_updated_at": _clean_optional_text(source_updated_at, "Source update time", 100),
        "source_payload": _clean_source_payload(source_payload),
    }
    now = _utc_now()
    created = _clean_timestamp(created_at, now)
    completed = now if clean_status == "done" else None

    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if clean_status == "open" and clean_plan == "now":
            _demote_other_now(conn, values["id"], now)
        conn.execute(
            """
            INSERT INTO tasks(
                id, title, plan, status, estimate, due_date, due_at,
                due_timezone, due_language, source, external_id, project,
                priority, recurrence, source_updated_at, source_payload,
                created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["id"], values["title"], values["plan"], values["status"],
                values["estimate"], values["due_date"], values["due_at"],
                values["due_timezone"], values["due_language"], values["source"],
                values["external_id"], values["project"], values["priority"],
                values["recurrence"], values["source_updated_at"], values["source_payload"],
                created, now, completed,
            ),
        )
        _bump_revision(conn)
        conn.commit()
        return _read_board(conn)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        message = "Task already exists"
        if clean_source and clean_external:
            message = f"Task already imported from {clean_source}: {clean_external}"
        raise BoardError(message) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_task(task_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "dueDate": "due_date",
        "dueAt": "due_at",
        "dueTimezone": "due_timezone",
        "dueLanguage": "due_language",
        "externalId": "external_id",
        "sourceUpdatedAt": "source_updated_at",
        "sourcePayload": "source_payload",
    }
    normalised = {aliases.get(key, key): value for key, value in changes.items()}
    allowed = {
        "title", "plan", "status", "estimate", "due_date", "due_at",
        "due_timezone", "due_language", "source", "external_id", "project",
        "priority", "recurrence", "source_updated_at", "source_payload", "lane",
    }
    unknown = set(normalised) - allowed
    if unknown:
        raise BoardError(f"Unsupported task fields: {', '.join(sorted(unknown))}")
    if not normalised:
        return get_board()

    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if existing is None:
            raise KeyError(task_id)

        if "lane" in normalised:
            lane_plan, lane_status = _legacy_to_dimensions(normalised.pop("lane"))
            normalised.setdefault("plan", lane_plan)
            normalised.setdefault("status", lane_status)

        cleaners = {
            "title": _clean_title,
            "plan": lambda value: _clean_choice(value, VALID_PLANS, "plan"),
            "status": lambda value: _clean_choice(value, VALID_STATUSES, "status"),
            "estimate": _clean_estimate,
            "due_date": _clean_due_date,
            "due_at": _clean_due_at,
            "due_timezone": lambda value: _clean_optional_text(value, "Due timezone", 100),
            "due_language": lambda value: _clean_optional_text(value, "Due language", 50),
            "source": lambda value: _clean_optional_text(value, "Source", 100),
            "external_id": lambda value: _clean_optional_text(value, "External ID", 200),
            "project": lambda value: _clean_optional_text(value, "Project"),
            "priority": _clean_priority,
            "recurrence": lambda value: _clean_optional_text(value, "Recurrence"),
            "source_updated_at": lambda value: _clean_optional_text(value, "Source update time", 100),
            "source_payload": _clean_source_payload,
        }
        updates = {key: cleaners[key](value) for key, value in normalised.items()}
        final_plan = str(updates.get("plan", existing["plan"]))
        final_status = str(updates.get("status", existing["status"]))
        final_due_date = updates.get("due_date", existing["due_date"])
        final_due_at = updates.get("due_at", existing["due_at"])
        if final_due_date and final_due_at:
            if "due_date" in updates and "due_at" not in updates:
                updates["due_at"] = None
                final_due_at = None
            elif "due_at" in updates and "due_date" not in updates:
                updates["due_date"] = None
                final_due_date = None
            else:
                raise BoardError("A task cannot have both an all-day due date and a timed due value")
        final_source = updates.get("source", existing["source"])
        final_external = updates.get("external_id", existing["external_id"])
        if bool(final_source) != bool(final_external):
            raise BoardError("Source and external ID must be provided together")

        now = _utc_now()
        if final_status == "open" and final_plan == "now":
            _demote_other_now(conn, task_id, now)
        updates["updated_at"] = now
        if "status" in updates:
            updates["completed_at"] = now if final_status == "done" else None

        assignments = [f"{column} = ?" for column in updates]
        conn.execute(
            f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
            [*updates.values(), task_id],
        )
        _bump_revision(conn)
        conn.commit()
        return _read_board(conn)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise BoardError("Task update violates a board invariant") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_task(task_id: str) -> dict[str, Any]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if result.rowcount == 0:
            raise KeyError(task_id)
        _bump_revision(conn)
        conn.commit()
        return _read_board(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def import_tasks(tasks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Atomically insert missing tasks without changing previously imported rows.

    ``(source, external_id)`` is the durable import identity.  A repeated pull
    skips that row wholesale, deliberately preserving every user-owned Hermes Todo
    field (especially plan and status).  Any invalid new row rolls the complete
    batch back rather than leaving a half-imported board.
    """
    raw_tasks = list(tasks)
    inserted = 0
    skipped = 0
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        has_open_now = conn.execute(
            "SELECT 1 FROM tasks WHERE plan = 'now' AND status = 'open'"
        ).fetchone() is not None

        for index, raw in enumerate(raw_tasks):
            if not isinstance(raw, dict):
                raise BoardError(f"Import task {index + 1} must be an object")

            task_id = _clean_optional_text(raw.get("id"), "Task ID", 200) or uuid.uuid4().hex
            source = _clean_optional_text(raw.get("source"), "Source", 100)
            external_id = _clean_optional_text(raw.get("externalId"), "External ID", 200)
            if bool(source) != bool(external_id):
                raise BoardError("Source and external ID must be provided together")

            exists = conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not exists and source and external_id:
                exists = conn.execute(
                    "SELECT 1 FROM tasks WHERE source = ? AND external_id = ?",
                    (source, external_id),
                ).fetchone()
            if exists:
                skipped += 1
                continue

            legacy_plan, legacy_status = _legacy_to_dimensions(raw.get("lane"))
            plan = _clean_choice(raw.get("plan", legacy_plan), VALID_PLANS, "plan")
            status = _clean_choice(raw.get("status", legacy_status), VALID_STATUSES, "status")
            if plan == "now" and status == "open" and has_open_now:
                plan = "today"

            due_date = _clean_due_date(raw.get("dueDate"))
            due_at = _clean_due_at(raw.get("dueAt"))
            if due_date and due_at:
                raise BoardError("A task cannot have both an all-day due date and a timed due value")

            now = _utc_now()
            created_at = _clean_timestamp(raw.get("createdAt"), now)
            completed_at = now if status == "done" else None
            conn.execute(
                """
                INSERT INTO tasks(
                    id, title, plan, status, estimate, due_date, due_at,
                    due_timezone, due_language, source, external_id, project,
                    priority, recurrence, source_updated_at, source_payload,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    _clean_title(raw.get("title")),
                    plan,
                    status,
                    _clean_estimate(raw.get("estimate", DEFAULT_ESTIMATE)),
                    due_date,
                    due_at,
                    _clean_optional_text(raw.get("dueTimezone"), "Due timezone", 100),
                    _clean_optional_text(raw.get("dueLanguage"), "Due language", 50),
                    source,
                    external_id,
                    _clean_optional_text(raw.get("project"), "Project"),
                    _clean_priority(raw.get("priority")),
                    _clean_optional_text(raw.get("recurrence"), "Recurrence"),
                    _clean_optional_text(raw.get("sourceUpdatedAt"), "Source update time", 100),
                    _clean_source_payload(raw.get("sourcePayload")),
                    created_at,
                    now,
                    completed_at,
                ),
            )
            inserted += 1
            if plan == "now" and status == "open":
                has_open_now = True

        if inserted:
            _bump_revision(conn)
        conn.commit()
        board = _read_board(conn)
        board["imported"] = inserted
        board["skipped"] = skipped
        return board
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise BoardError("Import violates a board invariant") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
