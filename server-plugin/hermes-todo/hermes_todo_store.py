"""Durable profile-scoped store for Hermes Todo.

Plan, workflow status, and deadline remain independent. SQLite is the shared
authority for Desktop, CLI, and optional JSON imports. Schema migrations are
automatic and additive for v3 boards.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from hermes_constants import get_hermes_home as _get_hermes_home
except ImportError:  # Standalone source-tree execution and tests.
    _get_hermes_home = None

VALID_PLANS = frozenset({"now", "today", "later"})
VALID_STATUSES = frozenset({"open", "waiting", "blocked", "done"})
VALID_EXECUTION_MODES = frozenset({"manual", "supervised", "autonomous"})
VALID_APPROVAL_STATES = frozenset({"not-required", "pending", "approved", "rejected"})
VALID_SESSION_STATES = frozenset({"active", "completed"})
VALID_RECURRENCE_RULES = frozenset({"daily", "weekdays", "weekly", "monthly"})
DEFAULT_ESTIMATE = 25
MAX_SOURCE_PAYLOAD_BYTES = 64 * 1024
MAX_EVENT_DATA_BYTES = 64 * 1024
MAX_ARTEFACTS = 20
MAX_ARTEFACT_LENGTH = 1000
SCHEMA_VERSION = 4
_UNSET = object()


class BoardError(ValueError):
    """A user-correctable board mutation error."""


class RevisionConflict(BoardError):
    """An optimistic write was based on an older board revision."""

    def __init__(self, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            f"Revision conflict: expected {expected_revision}, current revision is {current_revision}"
        )


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


def _create_v4_tasks(conn: sqlite3.Connection, table: str = "tasks") -> None:
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
            brief TEXT,
            next_action TEXT,
            closure_condition TEXT,
            waiting_on TEXT,
            review_date TEXT,
            blocker TEXT,
            artefacts TEXT NOT NULL DEFAULT '[]',
            owner TEXT,
            execution_mode TEXT NOT NULL DEFAULT 'manual'
                CHECK (execution_mode IN ('manual', 'supervised', 'autonomous')),
            approval_state TEXT NOT NULL DEFAULT 'not-required'
                CHECK (approval_state IN ('not-required', 'pending', 'approved', 'rejected')),
            inbox INTEGER NOT NULL DEFAULT 0 CHECK (inbox IN (0, 1)),
            closure_note TEXT,
            closure_evidence TEXT NOT NULL DEFAULT '[]',
            recurrence_rule TEXT,
            recurrence_timezone TEXT,
            series_id TEXT,
            occurrence_id TEXT,
            occurrence_number INTEGER,
            session_id TEXT,
            session_state TEXT CHECK (session_state IS NULL OR session_state IN ('active', 'completed')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            CHECK (due_date IS NULL OR due_at IS NULL),
            CHECK ((source IS NULL) = (external_id IS NULL))
        )
        """
    )


def _create_v4_support(conn: sqlite3.Connection) -> None:
    statements = (
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_v4_single_open_now
        ON tasks((1)) WHERE plan = 'now' AND status = 'open'""",
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_v4_source_external
        ON tasks(source, external_id)
        WHERE source IS NOT NULL AND external_id IS NOT NULL""",
        """CREATE INDEX IF NOT EXISTS idx_tasks_v4_status_plan_due
        ON tasks(status, plan, due_date, due_at, created_at)""",
        """CREATE INDEX IF NOT EXISTS idx_tasks_v4_agenda
        ON tasks(inbox, review_date, updated_at)""",
        """CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT,
            source TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )""",
        """CREATE INDEX IF NOT EXISTS idx_task_events_task_created
        ON task_events(task_id, created_at, id)""",
    )
    for statement in statements:
        conn.execute(statement)
    try:
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_v4_occurrence
            ON tasks(occurrence_id) WHERE occurrence_id IS NOT NULL"""
        )
    except sqlite3.IntegrityError:
        # Older databases may already contain duplicate optional recurrence
        # metadata. Runtime identity checks protect new writes without making
        # an otherwise readable profile fail to open.
        pass


_V4_ADDITIONS = {
    "brief": "TEXT",
    "next_action": "TEXT",
    "closure_condition": "TEXT",
    "waiting_on": "TEXT",
    "review_date": "TEXT",
    "blocker": "TEXT",
    "artefacts": "TEXT NOT NULL DEFAULT '[]'",
    "owner": "TEXT",
    "execution_mode": "TEXT NOT NULL DEFAULT 'manual'",
    "approval_state": "TEXT NOT NULL DEFAULT 'not-required'",
    "inbox": "INTEGER NOT NULL DEFAULT 0",
    "closure_note": "TEXT",
    "closure_evidence": "TEXT NOT NULL DEFAULT '[]'",
    "recurrence_rule": "TEXT",
    "recurrence_timezone": "TEXT",
    "series_id": "TEXT",
    "occurrence_id": "TEXT",
    "occurrence_number": "INTEGER",
    "session_id": "TEXT",
    "session_state": "TEXT",
}


def _append_event(
    conn: sqlite3.Connection,
    task_id: str,
    event_type: str,
    *,
    data: dict[str, Any] | None = None,
    actor: str | None = None,
    source: str = "store",
    created_at: str | None = None,
) -> None:
    clean_type = _clean_optional_text(event_type, "Event type", 100)
    clean_source = _clean_optional_text(source, "Event source", 100)
    if clean_type is None or clean_source is None:
        raise BoardError("Event type and source are required")
    clean_actor = _clean_optional_text(actor, "Event actor", 200)
    try:
        data_json = json.dumps(data or {}, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise BoardError("Event data must be JSON-compatible") from exc
    if len(data_json.encode("utf-8")) > MAX_EVENT_DATA_BYTES:
        raise BoardError(f"Event data must be {MAX_EVENT_DATA_BYTES} UTF-8 bytes or fewer")
    conn.execute(
        """
        INSERT INTO task_events(task_id, event_type, actor, source, data_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (task_id, clean_type, clean_actor, clean_source, data_json, created_at or _utc_now()),
    )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version >= SCHEMA_VERSION:
        _create_v4_support(conn)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
        ).fetchone()
        if not table_exists:
            _create_v4_tasks(conn)
        else:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "lane" in columns and "plan" not in columns:
                archive_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks_v2_archive'"
                ).fetchone()
                if archive_exists:
                    raise RuntimeError("Existing tasks_v2_archive prevents a safe migration")
                conn.execute("ALTER TABLE tasks RENAME TO tasks_v2_archive")
                _create_v4_tasks(conn)
                rows = conn.execute(
                    """
                    SELECT id, title, lane, estimate, created_at, updated_at, completed_at
                    FROM tasks_v2_archive ORDER BY created_at, id
                    """
                ).fetchall()
                kept_now = False
                for row in rows:
                    lane = str(row["lane"])
                    plan = "now" if lane == "now" and not kept_now else "today"
                    status = {"waiting": "waiting", "blocked": "blocked", "done": "done"}.get(
                        lane, "open"
                    )
                    if plan == "now" and status == "open":
                        kept_now = True
                    completed_at = row["completed_at"] if status == "done" else None
                    conn.execute(
                        """
                        INSERT INTO tasks(
                            id, title, plan, status, estimate,
                            created_at, updated_at, completed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["id"], row["title"], plan, status, row["estimate"],
                            row["created_at"], row["updated_at"], completed_at,
                        ),
                    )
                legacy_count = int(
                    conn.execute("SELECT COUNT(*) FROM tasks_v2_archive").fetchone()[0]
                )
                migrated_count = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
                if migrated_count != legacy_count:
                    raise RuntimeError("Hermes Todo migration row-count mismatch")
            elif {"plan", "status", "due_date", "due_at"}.issubset(columns):
                for name, declaration in _V4_ADDITIONS.items():
                    if name not in columns:
                        conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {declaration}")
            else:
                raise RuntimeError("Unversioned Hermes Todo schema cannot be migrated safely")

        _create_v4_support(conn)
        rows = conn.execute(
            "SELECT id, plan, status, inbox, occurrence_id, created_at FROM tasks"
        ).fetchall()
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM task_events WHERE task_id = ? AND event_type = 'task.created'",
                (row["id"],),
            ).fetchone()
            if not exists:
                _append_event(
                    conn,
                    str(row["id"]),
                    "task.created",
                    data={
                        "plan": row["plan"],
                        "status": row["status"],
                        "inbox": bool(row["inbox"]),
                        "occurrenceId": row["occurrence_id"],
                        "legacy": True,
                    },
                    source="migration",
                    created_at=str(row["created_at"]),
                )
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
    if value < 5 or value > 480:
        raise BoardError("Estimate must be between 5 and 480 minutes")
    return value


def _clean_due_date(value: Any, field: str = "Due date") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BoardError(f"{field} must be text in YYYY-MM-DD format")
    text = value.strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise BoardError(f"{field} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise BoardError(f"{field} must be YYYY-MM-DD")
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


def _clean_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise BoardError(f"{field} must be true or false")
    return value


def _clean_priority(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BoardError("Priority must be a whole number from 1 to 4")
    if value < 1 or value > 4:
        raise BoardError("Priority must be from 1 to 4")
    return value


def _clean_timestamp(value: Any, fallback: str, field: str = "Created time") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, bool):
        raise BoardError(f"{field} must be an ISO timestamp or integer milliseconds")
    if isinstance(value, int):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
        except (OSError, OverflowError, ValueError) as exc:
            raise BoardError(f"{field} is outside the supported range") from exc
    if not isinstance(value, str):
        raise BoardError(f"{field} must be an ISO timestamp or integer milliseconds")
    text = value.strip()
    if not text:
        return fallback
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BoardError(f"{field} must be an ISO timestamp or integer milliseconds") from exc
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


def _clean_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BoardError(f"{field} must be a list of strings")
    if len(value) > MAX_ARTEFACTS:
        raise BoardError(f"{field} may contain at most {MAX_ARTEFACTS} items")
    cleaned: list[str] = []
    for item in value:
        text = _clean_optional_text(item, field, MAX_ARTEFACT_LENGTH)
        if text is not None and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def _clean_recurrence_rule(value: Any) -> str | None:
    text = _clean_optional_text(value, "Recurrence rule", 50)
    if text is None:
        return None
    rule = text.lower()
    if rule in VALID_RECURRENCE_RULES:
        return rule
    match = re.fullmatch(r"every:([1-9]\d{0,2})d", rule)
    if match and int(match.group(1)) <= 365:
        return rule
    raise BoardError(
        "Recurrence rule must be daily, weekdays, weekly, monthly, or every:<1-365>d"
    )


def _clean_timezone(value: Any, field: str = "Recurrence timezone") -> str | None:
    text = _clean_optional_text(value, field, 100)
    if text is None:
        return None
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise BoardError(f"{field} must be a valid IANA timezone") from exc
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
        "brief": row["brief"],
        "nextAction": row["next_action"],
        "closureCondition": row["closure_condition"],
        "waitingOn": row["waiting_on"],
        "reviewDate": row["review_date"],
        "blocker": row["blocker"],
        "artefacts": _json_list(row["artefacts"]),
        "owner": row["owner"],
        "executionMode": row["execution_mode"],
        "approvalState": row["approval_state"],
        "inbox": bool(row["inbox"]),
        "closureNote": row["closure_note"],
        "closureEvidence": _json_list(row["closure_evidence"]),
        "recurrenceRule": row["recurrence_rule"],
        "recurrenceTimezone": row["recurrence_timezone"],
        "seriesId": row["series_id"],
        "occurrenceId": row["occurrence_id"],
        "occurrenceNumber": row["occurrence_number"],
        "sessionId": row["session_id"],
        "sessionState": row["session_state"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"],
    }


def _revision(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM board_meta WHERE key = 'revision'").fetchone()
    return int(row["value"] if row else 0)


def _read_board(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
        ORDER BY
            CASE status WHEN 'open' THEN 0 WHEN 'waiting' THEN 1 WHEN 'blocked' THEN 2 ELSE 3 END,
            CASE plan WHEN 'now' THEN 0 WHEN 'today' THEN 1 ELSE 2 END,
            inbox DESC,
            COALESCE(due_date, due_at) IS NULL,
            COALESCE(due_date, due_at),
            created_at,
            id
        """
    ).fetchall()
    return {
        "version": SCHEMA_VERSION,
        "revision": _revision(conn),
        "tasks": [_row_to_task(row) for row in rows],
    }


def _bump_revision(conn: sqlite3.Connection) -> int:
    conn.execute("UPDATE board_meta SET value = value + 1 WHERE key = 'revision'")
    return _revision(conn)


def _check_expected_revision(conn: sqlite3.Connection, expected_revision: int | None) -> None:
    if expected_revision is None:
        return
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise BoardError("Expected revision must be a non-negative whole number")
    current = _revision(conn)
    if expected_revision != current:
        raise RevisionConflict(expected_revision, current)


def _demote_other_now(
    conn: sqlite3.Connection,
    task_id: str,
    now: str,
    *,
    actor: str | None,
    event_source: str,
) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM tasks WHERE status = 'open' AND plan = 'now' AND id <> ?",
        (task_id,),
    ).fetchall()
    affected = [str(row["id"]) for row in rows]
    for affected_id in affected:
        conn.execute(
            "UPDATE tasks SET plan = 'today', updated_at = ? WHERE id = ?",
            (now, affected_id),
        )
        _append_event(
            conn,
            affected_id,
            "task.plan_changed",
            data={"from": "now", "to": "today", "reason": "single-open-now"},
            actor=actor,
            source=event_source,
            created_at=now,
        )
    return affected


def _task_result(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    affected_ids: Iterable[str] = (),
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(task_id)
    result: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "revision": _revision(conn),
        "task": _row_to_task(row),
    }
    affected = []
    for affected_id in affected_ids:
        affected_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (affected_id,)).fetchone()
        if affected_row is not None:
            affected.append(_row_to_task(affected_row))
    if affected:
        result["affectedTasks"] = affected
    if extras:
        result.update(extras)
    return result


def _return_mutation(
    conn: sqlite3.Connection,
    result: dict[str, Any],
    return_board: bool,
) -> dict[str, Any]:
    if return_board:
        board = _read_board(conn)
        for key in (
            "affectedTasks",
            "generatedTask",
            "followUpTask",
            "imported",
            "skipped",
            "created",
            "deletedId",
        ):
            if key in result:
                board[key] = result[key]
        return board
    return result


def get_board() -> dict[str, Any]:
    with _connect() as conn:
        return _read_board(conn)


def get_task(task_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return {
            "version": SCHEMA_VERSION,
            "revision": _revision(conn),
            "task": _row_to_task(row),
        }


def _prepare_task_values(
    title: Any,
    *,
    estimate: Any = DEFAULT_ESTIMATE,
    plan: Any = None,
    status: Any = None,
    due_date: Any = None,
    due_at: Any = None,
    due_timezone: Any = None,
    due_language: Any = None,
    source: Any = None,
    external_id: Any = None,
    project: Any = None,
    priority: Any = None,
    recurrence: Any = None,
    source_updated_at: Any = None,
    source_payload: Any = None,
    lane: Any = None,
    task_id: Any = None,
    created_at: Any = None,
    updated_at: Any = None,
    completed_at: Any = None,
    brief: Any = None,
    next_action: Any = None,
    closure_condition: Any = None,
    waiting_on: Any = None,
    review_date: Any = None,
    blocker: Any = None,
    artefacts: Any = None,
    owner: Any = None,
    execution_mode: Any = "manual",
    approval_state: Any = "not-required",
    inbox: Any = False,
    closure_note: Any = None,
    closure_evidence: Any = None,
    recurrence_rule: Any = None,
    recurrence_timezone: Any = None,
    series_id: Any = None,
    occurrence_id: Any = None,
    occurrence_number: Any = None,
    session_id: Any = None,
    session_state: Any = None,
) -> dict[str, Any]:
    legacy_plan, legacy_status = _legacy_to_dimensions(lane)
    clean_plan = _clean_choice(plan if plan is not None else legacy_plan, VALID_PLANS, "plan")
    clean_status = _clean_choice(
        status if status is not None else legacy_status, VALID_STATUSES, "status"
    )
    clean_due_date = _clean_due_date(due_date)
    clean_due_at = _clean_due_at(due_at)
    if clean_due_date and clean_due_at:
        raise BoardError("A task cannot have both an all-day due date and a timed due value")
    clean_source = _clean_optional_text(source, "Source", 100)
    clean_external = _clean_optional_text(external_id, "External ID", 200)
    if bool(clean_source) != bool(clean_external):
        raise BoardError("Source and external ID must be provided together")
    rule = _clean_recurrence_rule(recurrence_rule)
    recurrence_zone = _clean_timezone(recurrence_timezone) if recurrence_timezone else None
    due_zone = _clean_optional_text(due_timezone, "Due timezone", 100)
    if rule and not (clean_due_date or clean_due_at):
        raise BoardError("Executable recurrence requires a due date or timed due value")
    if rule and recurrence_zone is None:
        recurrence_zone = _clean_timezone(due_zone or "UTC")
    clean_series = _clean_optional_text(series_id, "Series ID", 200)
    clean_occurrence = _clean_optional_text(occurrence_id, "Occurrence ID", 240)
    if occurrence_number is not None and (
        isinstance(occurrence_number, bool)
        or not isinstance(occurrence_number, int)
        or occurrence_number < 1
    ):
        raise BoardError("Occurrence number must be a positive whole number")
    supplied_identity = (
        series_id is not None,
        occurrence_id is not None,
        occurrence_number is not None,
    )
    clean_identity = (
        clean_series is not None,
        clean_occurrence is not None,
        occurrence_number is not None,
    )
    if any(supplied_identity) and not all(clean_identity):
        raise BoardError(
            "Series ID, occurrence ID, and occurrence number must be provided together"
        )
    if all(clean_identity) and clean_occurrence != f"{clean_series}:{occurrence_number}":
        raise BoardError("Occurrence ID must match <seriesId>:<occurrenceNumber>")
    if rule and not any(supplied_identity):
        clean_series = uuid.uuid4().hex
        occurrence_number = 1
        clean_occurrence = f"{clean_series}:1"
    clean_session_state = None
    if session_state is not None:
        clean_session_state = _clean_choice(session_state, VALID_SESSION_STATES, "session state")
    clean_session_id = _clean_optional_text(session_id, "Session ID", 300)
    if bool(clean_session_id) != bool(clean_session_state):
        raise BoardError("Session ID and session state must be provided together")
    now = _utc_now()
    clean_created_at = _clean_timestamp(created_at, now, "Created time")
    clean_updated_at = _clean_timestamp(updated_at, now, "Updated time")
    clean_completed_at = None
    if completed_at is not None and completed_at != "":
        clean_completed_at = _clean_timestamp(completed_at, now, "Completed time")
    if clean_status == "done" and clean_completed_at is None:
        clean_completed_at = now
    return {
        "id": _clean_optional_text(task_id, "Task ID", 200) or uuid.uuid4().hex,
        "title": _clean_title(title),
        "plan": clean_plan,
        "status": clean_status,
        "estimate": _clean_estimate(estimate),
        "due_date": clean_due_date,
        "due_at": clean_due_at,
        "due_timezone": due_zone,
        "due_language": _clean_optional_text(due_language, "Due language", 50),
        "source": clean_source,
        "external_id": clean_external,
        "project": _clean_optional_text(project, "Project"),
        "priority": _clean_priority(priority),
        "recurrence": _clean_optional_text(recurrence, "Recurrence"),
        "source_updated_at": _clean_optional_text(source_updated_at, "Source update time", 100),
        "source_payload": _clean_source_payload(source_payload),
        "brief": _clean_optional_text(brief, "Brief", 8000),
        "next_action": _clean_optional_text(next_action, "Next action", 2000),
        "closure_condition": _clean_optional_text(closure_condition, "Closure condition", 4000),
        "waiting_on": _clean_optional_text(waiting_on, "Waiting on", 1000),
        "review_date": _clean_due_date(review_date, "Review date"),
        "blocker": _clean_optional_text(blocker, "Blocker", 2000),
        "artefacts": json.dumps(_clean_string_list(artefacts, "Artefacts"), ensure_ascii=False),
        "owner": _clean_optional_text(owner, "Owner", 200),
        "execution_mode": _clean_choice(execution_mode, VALID_EXECUTION_MODES, "execution mode"),
        "approval_state": _clean_choice(approval_state, VALID_APPROVAL_STATES, "approval state"),
        "inbox": int(_clean_bool(inbox, "Inbox")),
        "closure_note": _clean_optional_text(closure_note, "Closure note", 4000),
        "closure_evidence": json.dumps(
            _clean_string_list(closure_evidence, "Closure evidence"), ensure_ascii=False
        ),
        "recurrence_rule": rule,
        "recurrence_timezone": recurrence_zone,
        "series_id": clean_series,
        "occurrence_id": clean_occurrence,
        "occurrence_number": occurrence_number,
        "session_id": clean_session_id,
        "session_state": clean_session_state,
        "created_at": clean_created_at,
        "updated_at": clean_updated_at,
        "completed_at": clean_completed_at if clean_status == "done" else None,
    }


_INSERT_COLUMNS = (
    "id", "title", "plan", "status", "estimate", "due_date", "due_at",
    "due_timezone", "due_language", "source", "external_id", "project",
    "priority", "recurrence", "source_updated_at", "source_payload", "brief",
    "next_action", "closure_condition", "waiting_on", "review_date", "blocker",
    "artefacts", "owner", "execution_mode", "approval_state", "inbox",
    "closure_note", "closure_evidence", "recurrence_rule", "recurrence_timezone",
    "series_id", "occurrence_id", "occurrence_number", "session_id", "session_state",
    "created_at", "updated_at", "completed_at",
)


def _insert_values(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    placeholders = ", ".join("?" for _ in _INSERT_COLUMNS)
    conn.execute(
        f"INSERT INTO tasks({', '.join(_INSERT_COLUMNS)}) VALUES ({placeholders})",
        [values[column] for column in _INSERT_COLUMNS],
    )


def create_task(
    title: str,
    *,
    expected_revision: int | None = None,
    actor: str | None = None,
    event_source: str = "store",
    return_board: bool = True,
    **fields: Any,
) -> dict[str, Any]:
    normalised_fields = {_ALIASES.get(key, key): value for key, value in fields.items()}
    values = _prepare_task_values(title, **normalised_fields)
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_expected_revision(conn, expected_revision)
        affected: list[str] = []
        if values["status"] == "open" and values["plan"] == "now":
            affected = _demote_other_now(
                conn,
                values["id"],
                values["updated_at"],
                actor=actor,
                event_source=event_source,
            )
        _insert_values(conn, values)
        _append_event(
            conn,
            values["id"],
            "task.created",
            data={
                "plan": values["plan"],
                "status": values["status"],
                "inbox": bool(values["inbox"]),
                "occurrenceId": values["occurrence_id"],
            },
            actor=actor,
            source=event_source,
            created_at=values["created_at"],
        )
        _bump_revision(conn)
        result = _task_result(conn, values["id"], affected_ids=affected)
        conn.commit()
        return _return_mutation(conn, result, return_board)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        message = "Task already exists"
        if values["source"] and values["external_id"]:
            message = f"Task already imported from {values['source']}: {values['external_id']}"
        raise BoardError(message) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_ALIASES = {
    "dueDate": "due_date",
    "dueAt": "due_at",
    "dueTimezone": "due_timezone",
    "dueLanguage": "due_language",
    "externalId": "external_id",
    "sourceUpdatedAt": "source_updated_at",
    "sourcePayload": "source_payload",
    "nextAction": "next_action",
    "closureCondition": "closure_condition",
    "waitingOn": "waiting_on",
    "reviewDate": "review_date",
    "executionMode": "execution_mode",
    "approvalState": "approval_state",
    "closureNote": "closure_note",
    "closureEvidence": "closure_evidence",
    "recurrenceRule": "recurrence_rule",
    "recurrenceTimezone": "recurrence_timezone",
}


def _normalise_changes(changes: dict[str, Any]) -> dict[str, Any]:
    normalised = {_ALIASES.get(key, key): value for key, value in changes.items()}
    allowed = {
        "title", "plan", "status", "estimate", "due_date", "due_at",
        "due_timezone", "due_language", "source", "external_id", "project",
        "priority", "recurrence", "source_updated_at", "source_payload", "lane",
        "brief", "next_action", "closure_condition", "waiting_on", "review_date",
        "blocker", "artefacts", "owner", "execution_mode", "approval_state", "inbox",
        "closure_note", "closure_evidence", "recurrence_rule", "recurrence_timezone",
    }
    unknown = set(normalised) - allowed
    if unknown:
        raise BoardError(f"Unsupported task fields: {', '.join(sorted(unknown))}")
    return normalised


def _clean_updates(existing: sqlite3.Row, changes: dict[str, Any]) -> dict[str, Any]:
    normalised = _normalise_changes(changes)
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
        "brief": lambda value: _clean_optional_text(value, "Brief", 8000),
        "next_action": lambda value: _clean_optional_text(value, "Next action", 2000),
        "closure_condition": lambda value: _clean_optional_text(value, "Closure condition", 4000),
        "waiting_on": lambda value: _clean_optional_text(value, "Waiting on", 1000),
        "review_date": lambda value: _clean_due_date(value, "Review date"),
        "blocker": lambda value: _clean_optional_text(value, "Blocker", 2000),
        "artefacts": lambda value: json.dumps(_clean_string_list(value, "Artefacts"), ensure_ascii=False),
        "owner": lambda value: _clean_optional_text(value, "Owner", 200),
        "execution_mode": lambda value: _clean_choice(value, VALID_EXECUTION_MODES, "execution mode"),
        "approval_state": lambda value: _clean_choice(value, VALID_APPROVAL_STATES, "approval state"),
        "inbox": lambda value: int(_clean_bool(value, "Inbox")),
        "closure_note": lambda value: _clean_optional_text(value, "Closure note", 4000),
        "closure_evidence": lambda value: json.dumps(
            _clean_string_list(value, "Closure evidence"), ensure_ascii=False
        ),
        "recurrence_rule": _clean_recurrence_rule,
        "recurrence_timezone": _clean_timezone,
    }
    updates = {key: cleaners[key](value) for key, value in normalised.items()}
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
    final_status = str(updates.get("status", existing["status"]))
    if existing["status"] == "blocked" and final_status == "open" and existing["blocker"]:
        if updates.get("blocker", existing["blocker"]) is not None:
            raise BoardError("Clear the blocker when reopening a blocked task")
    if existing["status"] == "waiting" and final_status == "open" and existing["waiting_on"]:
        if updates.get("waiting_on", existing["waiting_on"]) is not None:
            raise BoardError("Clear waitingOn when reopening a waiting task")
    final_rule = updates.get("recurrence_rule", existing["recurrence_rule"])
    if final_rule:
        if not (final_due_date or final_due_at):
            raise BoardError("Executable recurrence requires a due date or timed due value")
        zone = updates.get("recurrence_timezone", existing["recurrence_timezone"])
        if not zone:
            zone = existing["due_timezone"] or "UTC"
        updates["recurrence_timezone"] = _clean_timezone(zone)
        if not existing["series_id"]:
            series_id = uuid.uuid4().hex
            updates["series_id"] = series_id
            updates["occurrence_number"] = 1
            updates["occurrence_id"] = f"{series_id}:1"
    return updates


def _event_value(column: str, value: Any) -> Any:
    if column in {"artefacts", "closure_evidence"}:
        return _json_list(value)
    if column == "inbox":
        return bool(value)
    if column == "source_payload":
        return "changed"
    return value


def _record_update_events(
    conn: sqlite3.Connection,
    task_id: str,
    existing: sqlite3.Row,
    updates: dict[str, Any],
    *,
    actor: str | None,
    event_source: str,
    now: str,
) -> None:
    changed = {
        column: {"from": _event_value(column, existing[column]), "to": _event_value(column, value)}
        for column, value in updates.items()
        if column not in {"updated_at", "completed_at", "session_state"}
        and existing[column] != value
    }
    if not changed:
        return
    if "title" in changed:
        _append_event(
            conn, task_id, "task.retitled", data=changed["title"], actor=actor,
            source=event_source, created_at=now,
        )
    if "plan" in changed:
        _append_event(
            conn, task_id, "task.plan_changed", data=changed["plan"], actor=actor,
            source=event_source, created_at=now,
        )
    if "status" in changed:
        _append_event(
            conn, task_id, "task.status_changed", data=changed["status"], actor=actor,
            source=event_source, created_at=now,
        )
    waiting_fields = {key: changed[key] for key in ("waiting_on", "review_date") if key in changed}
    if waiting_fields or (
        "status" in changed and ({changed["status"]["from"], changed["status"]["to"]} & {"waiting"})
    ):
        _append_event(
            conn, task_id, "task.waiting_changed", data=waiting_fields or changed["status"],
            actor=actor, source=event_source, created_at=now,
        )
    if "blocker" in changed or (
        "status" in changed and ({changed["status"]["from"], changed["status"]["to"]} & {"blocked"})
    ):
        _append_event(
            conn, task_id, "task.blocker_changed",
            data=changed.get("blocker", changed.get("status", {})), actor=actor,
            source=event_source, created_at=now,
        )
    if "artefacts" in changed:
        previous = set(changed["artefacts"]["from"])
        for artefact in changed["artefacts"]["to"]:
            if artefact not in previous:
                _append_event(
                    conn, task_id, "artefact.attached", data={"artefact": artefact},
                    actor=actor, source=event_source, created_at=now,
                )
    if "status" in changed and changed["status"]["to"] == "done":
        _append_event(
            conn,
            task_id,
            "task.completed",
            data={
                "closureNote": updates.get("closure_note", existing["closure_note"]),
                "closureEvidence": _json_list(
                    updates.get("closure_evidence", existing["closure_evidence"])
                ),
            },
            actor=actor,
            source=event_source,
            created_at=now,
        )
    if "status" in changed and changed["status"]["from"] == "done" and changed["status"]["to"] != "done":
        _append_event(
            conn, task_id, "task.reopened", data={"status": changed["status"]["to"]},
            actor=actor, source=event_source, created_at=now,
        )
    generic = {
        key: value
        for key, value in changed.items()
        if key not in {"title", "plan", "status", "waiting_on", "review_date", "blocker", "artefacts"}
    }
    if generic:
        _append_event(
            conn, task_id, "task.edited", data={"changes": generic}, actor=actor,
            source=event_source, created_at=now,
        )


def _advance_date(value: date, rule: str) -> date:
    if rule == "daily":
        return value + timedelta(days=1)
    if rule == "weekdays":
        candidate = value + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate
    if rule == "weekly":
        return value + timedelta(days=7)
    if rule == "monthly":
        year = value.year + (1 if value.month == 12 else 0)
        month = 1 if value.month == 12 else value.month + 1
        return date(year, month, min(value.day, monthrange(year, month)[1]))
    match = re.fullmatch(r"every:(\d{1,3})d", rule)
    if match:
        return value + timedelta(days=int(match.group(1)))
    raise BoardError("Task has an unsupported recurrence rule")


def _next_recurrence_values(row: sqlite3.Row) -> tuple[str | None, str | None]:
    rule = str(row["recurrence_rule"])
    if row["due_date"]:
        return _advance_date(date.fromisoformat(str(row["due_date"])), rule).isoformat(), None
    if row["due_at"]:
        parsed = datetime.fromisoformat(str(row["due_at"]).replace("Z", "+00:00"))
        zone = ZoneInfo(str(row["recurrence_timezone"] or row["due_timezone"] or "UTC"))
        local = parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)
        next_day = _advance_date(local.date(), rule)
        local_naive = local.replace(tzinfo=None)
        candidate_naive = local_naive.replace(
            year=next_day.year, month=next_day.month, day=next_day.day
        )
        advanced = candidate_naive.replace(tzinfo=zone, fold=local.fold)
        normalised = advanced.astimezone(timezone.utc).astimezone(zone)
        if normalised.replace(tzinfo=None) != candidate_naive:
            advanced = normalised
        return None, advanced.isoformat(timespec="seconds")
    raise BoardError("Executable recurrence requires a due date or timed due value")


def _generate_next_occurrence(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    actor: str | None,
    event_source: str,
    now: str,
) -> tuple[dict[str, Any] | None, bool]:
    if not row["recurrence_rule"]:
        return None, False
    series_id = str(row["series_id"])
    number = int(row["occurrence_number"] or 1) + 1
    occurrence_id = f"{series_id}:{number}"
    existing = conn.execute(
        "SELECT * FROM tasks WHERE occurrence_id = ?", (occurrence_id,)
    ).fetchone()
    if existing is not None:
        return _row_to_task(existing), False
    next_due_date, next_due_at = _next_recurrence_values(row)
    values = {
        "id": uuid.uuid4().hex,
        "title": row["title"],
        "plan": "today" if row["plan"] == "now" else row["plan"],
        "status": "open",
        "estimate": row["estimate"],
        "due_date": next_due_date,
        "due_at": next_due_at,
        "due_timezone": row["due_timezone"],
        "due_language": row["due_language"],
        "source": None,
        "external_id": None,
        "project": row["project"],
        "priority": row["priority"],
        "recurrence": row["recurrence"],
        "source_updated_at": None,
        "source_payload": None,
        "brief": row["brief"],
        "next_action": row["next_action"],
        "closure_condition": row["closure_condition"],
        "waiting_on": None,
        "review_date": None,
        "blocker": None,
        "artefacts": "[]",
        "owner": row["owner"],
        "execution_mode": row["execution_mode"],
        "approval_state": row["approval_state"],
        "inbox": 0,
        "closure_note": None,
        "closure_evidence": "[]",
        "recurrence_rule": row["recurrence_rule"],
        "recurrence_timezone": row["recurrence_timezone"],
        "series_id": series_id,
        "occurrence_id": occurrence_id,
        "occurrence_number": number,
        "session_id": None,
        "session_state": None,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    _insert_values(conn, values)
    _append_event(
        conn,
        values["id"],
        "task.created",
        data={
            "plan": values["plan"],
            "status": "open",
            "inbox": False,
            "occurrenceId": occurrence_id,
            "generatedFrom": row["id"],
        },
        actor=actor,
        source="recurrence",
        created_at=now,
    )
    _append_event(
        conn,
        str(row["id"]),
        "recurrence.generated",
        data={"taskId": values["id"], "occurrenceId": occurrence_id},
        actor=actor,
        source=event_source,
        created_at=now,
    )
    generated_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (values["id"],)).fetchone()
    return _row_to_task(generated_row), True


def update_task(
    task_id: str,
    changes: dict[str, Any],
    *,
    expected_revision: int | None = None,
    actor: str | None = None,
    event_source: str = "store",
    return_board: bool = True,
) -> dict[str, Any]:
    normalised = _normalise_changes(changes)
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_expected_revision(conn, expected_revision)
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if existing is None:
            raise KeyError(task_id)
        updates = _clean_updates(existing, normalised)
        now = _utc_now()
        final_plan = str(updates.get("plan", existing["plan"]))
        final_status = str(updates.get("status", existing["status"]))
        affected: list[str] = []
        if final_status == "open" and final_plan == "now":
            affected = _demote_other_now(
                conn, task_id, now, actor=actor, event_source=event_source
            )
        changed = any(existing[key] != value for key, value in updates.items())
        generated: dict[str, Any] | None = None
        if changed:
            updates["updated_at"] = now
            if "status" in updates:
                updates["completed_at"] = now if final_status == "done" else None
                if final_status == "done" and existing["session_state"] == "active":
                    updates["session_state"] = "completed"
            assignments = [f"{column} = ?" for column in updates]
            conn.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
                [*updates.values(), task_id],
            )
            _record_update_events(
                conn, task_id, existing, updates, actor=actor,
                event_source=event_source, now=now,
            )
            if updates.get("session_state") == "completed":
                _append_event(
                    conn, task_id, "session.completed",
                    data={"sessionId": existing["session_id"], "reason": "task-completed"},
                    actor=actor, source=event_source, created_at=now,
                )
            current = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if existing["status"] != "done" and final_status == "done":
                generated, _ = _generate_next_occurrence(
                    conn, current, actor=actor, event_source=event_source, now=now
                )
            _bump_revision(conn)
        extras = {"generatedTask": generated} if generated else None
        result = _task_result(conn, task_id, affected_ids=affected, extras=extras)
        conn.commit()
        return _return_mutation(conn, result, return_board)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise BoardError("Task update violates a board invariant") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_task(
    task_id: str,
    *,
    expected_revision: int | None = None,
    actor: str | None = None,
    event_source: str = "store",
    return_board: bool = True,
) -> dict[str, Any]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_expected_revision(conn, expected_revision)
        row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        _append_event(conn, task_id, "task.deleted", actor=actor, source=event_source)
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        _bump_revision(conn)
        result = {
            "version": SCHEMA_VERSION,
            "revision": _revision(conn),
            "deletedId": task_id,
        }
        conn.commit()
        return _return_mutation(conn, result, return_board)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_history(task_id: str, *, limit: int = 100) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 200:
        raise BoardError("History limit must be from 1 to 200")
    with _connect() as conn:
        task_exists = conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone()
        event_exists = conn.execute(
            "SELECT 1 FROM task_events WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not task_exists and not event_exists:
            raise KeyError(task_id)
        rows = conn.execute(
            """
            SELECT id, task_id, event_type, actor, source, data_json, created_at
            FROM task_events WHERE task_id = ?
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (task_id, limit),
        ).fetchall()
        events = []
        for row in rows:
            try:
                data = json.loads(row["data_json"])
            except json.JSONDecodeError:
                data = {}
            events.append(
                {
                    "id": row["id"],
                    "taskId": row["task_id"],
                    "type": row["event_type"],
                    "actor": row["actor"],
                    "source": row["source"],
                    "data": data,
                    "createdAt": row["created_at"],
                }
            )
        return {
            "version": SCHEMA_VERSION,
            "revision": _revision(conn),
            "taskId": task_id,
            "events": events,
        }


def search_tasks(
    query: str | None = None,
    *,
    plan: str | None = None,
    status: str | None = None,
    project: str | None = None,
    owner: str | None = None,
    inbox: bool | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 500:
        raise BoardError("Search limit must be from 1 to 500")
    clean_query = _clean_optional_text(query, "Search query", 500)
    clean_plan = _clean_choice(plan, VALID_PLANS, "plan") if plan is not None else None
    clean_status = _clean_choice(status, VALID_STATUSES, "status") if status is not None else None
    clean_project = _clean_optional_text(project, "Project")
    clean_owner = _clean_optional_text(owner, "Owner", 200)
    if inbox is not None:
        inbox = _clean_bool(inbox, "Inbox")
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC, id").fetchall()
        matched = []
        needle = clean_query.casefold() if clean_query else None
        for row in rows:
            task = _row_to_task(row)
            haystack = " ".join(
                str(task.get(key) or "")
                for key in (
                    "title", "brief", "nextAction", "project", "owner", "waitingOn",
                    "blocker", "closureCondition", "closureNote",
                )
            ).casefold()
            if needle and needle not in haystack:
                continue
            if clean_plan and task["plan"] != clean_plan:
                continue
            if clean_status and task["status"] != clean_status:
                continue
            if clean_project and (task["project"] or "").casefold() != clean_project.casefold():
                continue
            if clean_owner and (task["owner"] or "").casefold() != clean_owner.casefold():
                continue
            if inbox is not None and task["inbox"] is not inbox:
                continue
            matched.append(task)
        return {
            "version": SCHEMA_VERSION,
            "revision": _revision(conn),
            "query": clean_query,
            "total": len(matched),
            "truncated": len(matched) > limit,
            "tasks": matched[:limit],
        }


_AGENDA_LABELS = {
    "now": "Current Now task",
    "inbox": "Needs triage",
    "overdue": "Past its due date",
    "due_today": "Due today",
    "ready_next": "Ready next",
    "waiting_review": "Waiting review is scheduled",
    "blocked": "Blocked with a recorded blocker",
    "stale": "No recent update",
}
_AGENDA_RANK = {code: index for index, code in enumerate(_AGENDA_LABELS)}


def _task_due_date(task: dict[str, Any], zone: ZoneInfo) -> date | None:
    if task["dueDate"]:
        return date.fromisoformat(task["dueDate"])
    if task["dueAt"]:
        parsed = datetime.fromisoformat(str(task["dueAt"]).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            source_zone = zone
            if task.get("dueTimezone"):
                try:
                    source_zone = ZoneInfo(str(task["dueTimezone"]))
                except (ZoneInfoNotFoundError, ValueError):
                    # Legacy rows may predate due-timezone validation. Treat an
                    # invalid value in the agenda zone instead of failing reads.
                    source_zone = zone
            parsed = parsed.replace(tzinfo=source_zone)
        return parsed.astimezone(zone).date()
    return None


def get_agenda(
    *,
    on_date: str | None = None,
    timezone_name: str = "UTC",
    stale_days: int = 14,
    limit: int = 50,
) -> dict[str, Any]:
    zone_name = _clean_timezone(timezone_name, "Agenda timezone") or "UTC"
    zone = ZoneInfo(zone_name)
    agenda_date = date.fromisoformat(_clean_due_date(on_date, "Agenda date")) if on_date else datetime.now(zone).date()
    if isinstance(stale_days, bool) or not isinstance(stale_days, int) or not 1 <= stale_days <= 365:
        raise BoardError("Stale days must be from 1 to 365")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise BoardError("Agenda limit must be from 1 to 200")
    stale_before = agenda_date - timedelta(days=stale_days)
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE status <> 'done'").fetchall()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            task = _row_to_task(row)
            reason_codes: list[str] = []
            if task["status"] == "open" and task["plan"] == "now":
                reason_codes.append("now")
            if task["inbox"]:
                reason_codes.append("inbox")
            due = _task_due_date(task, zone)
            if due and due < agenda_date:
                reason_codes.append("overdue")
            elif due == agenda_date:
                reason_codes.append("due_today")
            if task["status"] == "open" and task["plan"] == "today" and not task["inbox"]:
                reason_codes.append("ready_next")
            if task["status"] == "waiting" and task["reviewDate"]:
                try:
                    if date.fromisoformat(task["reviewDate"]) <= agenda_date:
                        reason_codes.append("waiting_review")
                except ValueError:
                    pass
            if task["status"] == "blocked" and task["blocker"]:
                reason_codes.append("blocked")
            try:
                updated = datetime.fromisoformat(str(task["updatedAt"]).replace("Z", "+00:00"))
                updated_date = updated.astimezone(zone).date() if updated.tzinfo else updated.date()
                if updated_date <= stale_before:
                    reason_codes.append("stale")
            except ValueError:
                pass
            if reason_codes:
                reasons = []
                for code in reason_codes:
                    label = _AGENDA_LABELS[code]
                    if code == "waiting_review":
                        label = f"Review waiting task on {task['reviewDate']}"
                    elif code == "blocked":
                        label = f"Blocked: {task['blocker']}"
                    reasons.append({"code": code, "label": label})
                candidates.append({"task": task, "reasons": reasons})
        candidates.sort(
            key=lambda item: (
                min(_AGENDA_RANK[reason["code"]] for reason in item["reasons"]),
                item["task"]["dueDate"] or item["task"]["dueAt"] or "9999",
                item["task"]["priority"] or 9,
                item["task"]["createdAt"],
            )
        )
        return {
            "version": SCHEMA_VERSION,
            "revision": _revision(conn),
            "generatedAt": _utc_now(),
            "date": agenda_date.isoformat(),
            "timezone": zone_name,
            "staleAfterDays": stale_days,
            "total": len(candidates),
            "truncated": len(candidates) > limit,
            "items": candidates[:limit],
        }


def link_task_session(
    task_id: str,
    session_id: str,
    *,
    start_now: bool = True,
    expected_revision: int | None = None,
    actor: str | None = None,
    event_source: str = "store",
    return_board: bool = False,
) -> dict[str, Any]:
    clean_session_id = _clean_optional_text(session_id, "Session ID", 300)
    if clean_session_id is None:
        raise BoardError("Session ID is required")
    start_now = _clean_bool(start_now, "Start now")
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_expected_revision(conn, expected_revision)
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if existing is None:
            raise KeyError(task_id)
        if existing["session_state"] == "active":
            if existing["session_id"] != clean_session_id:
                raise BoardError("Task already has an active linked session")
            result = _task_result(conn, task_id)
            conn.commit()
            return _return_mutation(conn, result, return_board)
        if existing["status"] != "open":
            raise BoardError("Reopen and clear waiting or blocking context before starting work")
        now = _utc_now()
        affected: list[str] = []
        plan = existing["plan"]
        if start_now:
            plan = "now"
            affected = _demote_other_now(
                conn, task_id, now, actor=actor, event_source=event_source
            )
        conn.execute(
            """
            UPDATE tasks SET session_id = ?, session_state = 'active', plan = ?, inbox = 0,
                updated_at = ? WHERE id = ?
            """,
            (clean_session_id, plan, now, task_id),
        )
        if start_now and existing["plan"] != "now":
            _append_event(
                conn, task_id, "task.plan_changed",
                data={"from": existing["plan"], "to": "now", "reason": "session-started"},
                actor=actor, source=event_source, created_at=now,
            )
        _append_event(
            conn, task_id, "session.started", data={"sessionId": clean_session_id},
            actor=actor, source=event_source, created_at=now,
        )
        _bump_revision(conn)
        result = _task_result(conn, task_id, affected_ids=affected)
        conn.commit()
        return _return_mutation(conn, result, return_board)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_task_session(
    task_id: str,
    *,
    expected_revision: int | None = None,
    actor: str | None = None,
    event_source: str = "store",
    return_board: bool = False,
) -> dict[str, Any]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_expected_revision(conn, expected_revision)
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if existing is None:
            raise KeyError(task_id)
        if existing["session_state"] != "active":
            result = _task_result(conn, task_id)
            conn.commit()
            return _return_mutation(conn, result, return_board)
        now = _utc_now()
        conn.execute(
            "UPDATE tasks SET session_state = 'completed', updated_at = ? WHERE id = ?",
            (now, task_id),
        )
        _append_event(
            conn, task_id, "session.completed", data={"sessionId": existing["session_id"]},
            actor=actor, source=event_source, created_at=now,
        )
        _bump_revision(conn)
        result = _task_result(conn, task_id)
        conn.commit()
        return _return_mutation(conn, result, return_board)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def generate_next_occurrence(
    task_id: str,
    *,
    expected_revision: int | None = None,
    actor: str | None = None,
    event_source: str = "store",
    return_board: bool = False,
) -> dict[str, Any]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_expected_revision(conn, expected_revision)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        if row["status"] != "done":
            raise BoardError("Complete the current occurrence before generating the next one")
        generated, created = _generate_next_occurrence(
            conn, row, actor=actor, event_source=event_source, now=_utc_now()
        )
        if created:
            _bump_revision(conn)
        result = _task_result(
            conn,
            task_id,
            extras={"generatedTask": generated, "created": created},
        )
        conn.commit()
        return _return_mutation(conn, result, return_board)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_with_follow_up(
    task_id: str,
    follow_up: dict[str, Any],
    *,
    closure_note: Any = _UNSET,
    closure_evidence: Any = _UNSET,
    expected_revision: int | None = None,
    actor: str | None = None,
    event_source: str = "store",
    return_board: bool = False,
) -> dict[str, Any]:
    if not isinstance(follow_up, dict):
        raise BoardError("Follow-up must be a task object")
    follow_fields = dict(follow_up)
    title = follow_fields.pop("title", None)
    follow_values = _prepare_task_values(title, **{_ALIASES.get(k, k): v for k, v in follow_fields.items()})
    note_was_provided = closure_note is not _UNSET
    evidence_was_provided = closure_evidence is not _UNSET
    clean_note = (
        _clean_optional_text(closure_note, "Closure note", 4000)
        if note_was_provided
        else None
    )
    clean_evidence = (
        json.dumps(
            _clean_string_list(closure_evidence, "Closure evidence"), ensure_ascii=False
        )
        if evidence_was_provided
        else None
    )
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_expected_revision(conn, expected_revision)
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if existing is None:
            raise KeyError(task_id)
        if existing["status"] == "done":
            raise BoardError("Task is already complete")
        now = _utc_now()
        final_note = clean_note if note_was_provided else existing["closure_note"]
        final_evidence = (
            clean_evidence if evidence_was_provided else (existing["closure_evidence"] or "[]")
        )
        session_state = "completed" if existing["session_state"] == "active" else existing["session_state"]
        conn.execute(
            """
            UPDATE tasks SET status = 'done', completed_at = ?, updated_at = ?,
                closure_note = ?, closure_evidence = ?, session_state = ? WHERE id = ?
            """,
            (now, now, final_note, final_evidence, session_state, task_id),
        )
        _append_event(
            conn, task_id, "task.status_changed",
            data={"from": existing["status"], "to": "done"}, actor=actor,
            source=event_source, created_at=now,
        )
        _append_event(
            conn, task_id, "task.completed",
            data={"closureNote": final_note, "closureEvidence": json.loads(final_evidence)},
            actor=actor, source=event_source, created_at=now,
        )
        if existing["session_state"] == "active":
            _append_event(
                conn, task_id, "session.completed",
                data={"sessionId": existing["session_id"], "reason": "task-completed"},
                actor=actor, source=event_source, created_at=now,
            )
        current = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        generated, _ = _generate_next_occurrence(
            conn, current, actor=actor, event_source=event_source, now=now
        )
        affected: list[str] = []
        if follow_values["status"] == "open" and follow_values["plan"] == "now":
            affected = _demote_other_now(
                conn, follow_values["id"], now, actor=actor, event_source=event_source
            )
        _insert_values(conn, follow_values)
        _append_event(
            conn, follow_values["id"], "task.created",
            data={"plan": follow_values["plan"], "status": follow_values["status"], "inbox": bool(follow_values["inbox"]), "followUpTo": task_id},
            actor=actor, source=event_source, created_at=follow_values["created_at"],
        )
        _bump_revision(conn)
        follow_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (follow_values["id"],)).fetchone()
        result = _task_result(
            conn,
            task_id,
            affected_ids=affected,
            extras={
                "followUpTask": _row_to_task(follow_row),
                "generatedTask": generated,
            },
        )
        conn.commit()
        return _return_mutation(conn, result, return_board)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise BoardError("Complete-and-follow-up violates a board invariant") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_IMPORT_KEYS = {
    "id", "title", "lane", "plan", "status", "estimate", "dueDate", "dueAt",
    "dueTimezone", "dueLanguage", "source", "externalId", "project", "priority",
    "recurrence", "sourceUpdatedAt", "sourcePayload", "createdAt", "brief",
    "nextAction", "closureCondition", "waitingOn", "reviewDate", "blocker", "artefacts",
    "owner", "executionMode", "approvalState", "inbox", "closureNote", "closureEvidence",
    "recurrenceRule", "recurrenceTimezone", "seriesId", "occurrenceId", "occurrenceNumber",
    "sessionId", "sessionState", "updatedAt", "completedAt",
}


def _validate_import_occurrence_identity(raw: dict[str, Any]) -> None:
    identity_keys = {"seriesId", "occurrenceId", "occurrenceNumber"}
    supplied = identity_keys.intersection(raw)
    if supplied and supplied != identity_keys:
        raise BoardError(
            "Imported seriesId, occurrenceId, and occurrenceNumber must be provided together"
        )
    if not supplied:
        return
    series_id = _clean_optional_text(raw.get("seriesId"), "Series ID", 200)
    occurrence_id = _clean_optional_text(raw.get("occurrenceId"), "Occurrence ID", 240)
    occurrence_number = raw.get("occurrenceNumber")
    if (
        series_id is None
        or occurrence_id is None
        or isinstance(occurrence_number, bool)
        or not isinstance(occurrence_number, int)
        or occurrence_number < 1
    ):
        raise BoardError(
            "Imported seriesId, occurrenceId, and occurrenceNumber must form a complete identity"
        )
    if occurrence_id != f"{series_id}:{occurrence_number}":
        raise BoardError("Imported occurrenceId must match <seriesId>:<occurrenceNumber>")


def _import_source_payload(raw: dict[str, Any]) -> Any:
    unknown = {key: value for key, value in raw.items() if key not in _IMPORT_KEYS}
    payload = raw.get("sourcePayload")
    if not unknown:
        return payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {"value": payload}
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    payload = dict(payload)
    if "legacyFields" not in payload:
        payload["legacyFields"] = unknown
    elif isinstance(payload["legacyFields"], dict):
        merged_legacy = dict(unknown)
        merged_legacy.update(payload["legacyFields"])
        payload["legacyFields"] = merged_legacy
    else:
        existing_imported = payload.get("importedTopLevelFields")
        merged_imported = dict(unknown)
        if isinstance(existing_imported, dict):
            merged_imported.update(existing_imported)
        payload["importedTopLevelFields"] = merged_imported
    return payload


def import_tasks(
    tasks: Iterable[dict[str, Any]],
    *,
    expected_revision: int | None = None,
    actor: str | None = None,
    event_source: str = "import",
    return_board: bool = True,
) -> dict[str, Any]:
    """Atomically insert missing tasks without changing previously imported rows."""
    raw_tasks = list(tasks)
    inserted = 0
    skipped = 0
    inserted_ids: list[str] = []
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_expected_revision(conn, expected_revision)
        has_open_now = conn.execute(
            "SELECT 1 FROM tasks WHERE plan = 'now' AND status = 'open'"
        ).fetchone() is not None
        for index, raw in enumerate(raw_tasks):
            if not isinstance(raw, dict):
                raise BoardError(f"Import task {index + 1} must be an object")
            _validate_import_occurrence_identity(raw)
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
            values = _prepare_task_values(
                raw.get("title"),
                task_id=task_id,
                lane=raw.get("lane"),
                plan=raw.get("plan"),
                status=raw.get("status"),
                estimate=raw.get("estimate", DEFAULT_ESTIMATE),
                due_date=raw.get("dueDate"),
                due_at=raw.get("dueAt"),
                due_timezone=raw.get("dueTimezone"),
                due_language=raw.get("dueLanguage"),
                source=source,
                external_id=external_id,
                project=raw.get("project"),
                priority=raw.get("priority"),
                recurrence=raw.get("recurrence"),
                source_updated_at=raw.get("sourceUpdatedAt"),
                source_payload=_import_source_payload(raw),
                created_at=raw.get("createdAt"),
                updated_at=raw.get("updatedAt"),
                completed_at=raw.get("completedAt"),
                brief=raw.get("brief"),
                next_action=raw.get("nextAction"),
                closure_condition=raw.get("closureCondition"),
                waiting_on=raw.get("waitingOn"),
                review_date=raw.get("reviewDate"),
                blocker=raw.get("blocker"),
                artefacts=raw.get("artefacts"),
                owner=raw.get("owner"),
                execution_mode=raw.get("executionMode", "manual"),
                approval_state=raw.get("approvalState", "not-required"),
                inbox=raw.get("inbox", False),
                closure_note=raw.get("closureNote"),
                closure_evidence=raw.get("closureEvidence"),
                recurrence_rule=raw.get("recurrenceRule"),
                recurrence_timezone=raw.get("recurrenceTimezone"),
                series_id=raw.get("seriesId"),
                occurrence_id=raw.get("occurrenceId"),
                occurrence_number=raw.get("occurrenceNumber"),
                session_id=raw.get("sessionId"),
                session_state=raw.get("sessionState"),
            )
            if values["occurrence_id"]:
                duplicate_occurrence = conn.execute(
                    "SELECT id FROM tasks WHERE occurrence_id = ?",
                    (values["occurrence_id"],),
                ).fetchone()
                if duplicate_occurrence is not None:
                    raise BoardError(
                        f"Occurrence identity already exists: {values['occurrence_id']}"
                    )
            if values["plan"] == "now" and values["status"] == "open" and has_open_now:
                values["plan"] = "today"
            _insert_values(conn, values)
            _append_event(
                conn, values["id"], "task.created",
                data={"plan": values["plan"], "status": values["status"], "inbox": bool(values["inbox"]), "imported": True},
                actor=actor, source=event_source, created_at=values["created_at"],
            )
            inserted += 1
            inserted_ids.append(values["id"])
            if values["plan"] == "now" and values["status"] == "open":
                has_open_now = True
        if inserted:
            _bump_revision(conn)
        result = {
            "version": SCHEMA_VERSION,
            "revision": _revision(conn),
            "imported": inserted,
            "skipped": skipped,
            "tasks": [
                _row_to_task(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
                for task_id in inserted_ids
            ],
        }
        conn.commit()
        return _return_mutation(conn, result, return_board)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise BoardError("Import violates a board invariant") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
