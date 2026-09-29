from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN_ROOT))

import hermes_todo_store as store
from hermes_todo_store import (
    BoardError,
    MAX_SOURCE_PAYLOAD_BYTES,
    RevisionConflict,
    complete_task_session,
    complete_with_follow_up,
    create_task,
    generate_next_occurrence,
    get_agenda,
    get_board,
    get_history,
    import_tasks,
    link_task_session,
    resolve_db_path,
    search_tasks,
    update_task,
)


class HermesTodoStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_home = os.environ.get("HERMES_HOME")
        self.previous_home_resolver = store._get_hermes_home
        store._get_hermes_home = None
        os.environ["HERMES_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self.previous_home
        store._get_hermes_home = self.previous_home_resolver
        self.tmp.cleanup()

    def test_uses_host_home_resolver_when_available(self) -> None:
        expected = Path(self.tmp.name) / "host-profile"
        store._get_hermes_home = lambda: expected
        self.assertEqual(store.resolve_hermes_home(), expected)

    def test_database_directory_and_sqlite_files_are_private(self) -> None:
        old_umask = os.umask(0)
        try:
            conn = store._connect()
        finally:
            os.umask(old_umask)
        try:
            path = resolve_db_path()
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                self.assertTrue(candidate.is_file(), candidate)
                self.assertEqual(candidate.stat().st_mode & 0o777, 0o600)
        finally:
            conn.close()

    def _create_v2_database(self) -> None:
        path = resolve_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE board_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            INSERT INTO board_meta(key, value) VALUES ('revision', 7);
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                lane TEXT NOT NULL CHECK (lane IN ('now', 'today', 'waiting', 'done')),
                estimate INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            INSERT INTO tasks VALUES
                ('n', 'Existing Now', 'now', 25, '2026-08-01T09:00:00Z', '2026-08-01T09:00:00Z', NULL),
                ('w', 'Existing Waiting', 'waiting', 15, '2026-08-01T10:00:00Z', '2026-08-01T10:00:00Z', NULL),
                ('d', 'Existing Done', 'done', 45, '2026-08-01T11:00:00Z', '2026-08-01T12:00:00Z', '2026-08-01T12:00:00Z');
            """
        )
        conn.commit()
        conn.close()

    def test_migrates_v2_without_losing_tasks_or_revision(self) -> None:
        self._create_v2_database()
        board = get_board()
        self.assertEqual(board["version"], 5)
        self.assertEqual(board["revision"], 7)
        self.assertEqual(len(board["tasks"]), 3)
        by_id = {task["id"]: task for task in board["tasks"]}
        self.assertEqual((by_id["n"]["plan"], by_id["n"]["status"]), ("now", "open"))
        self.assertEqual(by_id["w"]["status"], "waiting")
        self.assertEqual(by_id["d"]["status"], "done")
        self.assertIsNone(by_id["n"]["dueDate"])
        self.assertIsNone(by_id["n"]["dueAt"])
        self.assertEqual(by_id["n"]["category"], "today")
        conn = sqlite3.connect(resolve_db_path())
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 5)
        finally:
            conn.close()

    def test_failed_migration_rolls_back_without_touching_v2(self) -> None:
        self._create_v2_database()
        conn = sqlite3.connect(resolve_db_path())
        conn.execute("UPDATE tasks SET estimate = 1 WHERE id = 'w'")
        conn.commit()
        conn.close()

        with self.assertRaises(sqlite3.IntegrityError):
            get_board()

        conn = sqlite3.connect(resolve_db_path())
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
            self.assertIn("lane", columns)
            self.assertNotIn("plan", columns)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 3)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 0)
        finally:
            conn.close()

    def test_plan_status_and_due_are_independent(self) -> None:
        board = create_task("Deadline", plan="later", status="blocked", due_date="2026-08-07")
        task = board["tasks"][0]
        self.assertEqual((task["plan"], task["status"], task["lane"]), ("later", "blocked", "blocked"))
        self.assertEqual(task["dueDate"], "2026-08-07")
        self.assertIsNone(task["dueAt"])
        reopened = update_task(task["id"], {"status": "open"})
        task = reopened["tasks"][0]
        self.assertEqual((task["plan"], task["status"], task["lane"]), ("later", "open", "later"))
        cleared = update_task(task["id"], {"dueDate": None})
        self.assertIsNone(cleared["tasks"][0]["dueDate"])

    def test_only_one_open_now_task(self) -> None:
        first = create_task("First", plan="now")
        first_id = first["tasks"][0]["id"]
        second = create_task("Second", plan="now")
        open_now = [task for task in second["tasks"] if task["status"] == "open" and task["plan"] == "now"]
        self.assertEqual(len(open_now), 1)
        self.assertEqual(next(task for task in second["tasks"] if task["id"] == first_id)["plan"], "today")
        blocked = update_task(open_now[0]["id"], {"status": "blocked"})
        self.assertEqual([task for task in blocked["tasks"] if task["status"] == "open" and task["plan"] == "now"], [])

    def test_source_identity_is_idempotent(self) -> None:
        create_task("Imported", source="external", external_id="abc", project="Work", priority=4)
        with self.assertRaises(BoardError):
            create_task("Duplicate", source="external", external_id="abc")
        board = import_tasks(
            [
                {"title": "Duplicate import", "source": "external", "externalId": "abc"},
                {"title": "New import", "source": "external", "externalId": "def", "plan": "later"},
            ]
        )
        self.assertEqual(board["imported"], 1)
        self.assertEqual(board["skipped"], 1)
        self.assertEqual(len(board["tasks"]), 2)

    def test_repeat_import_preserves_user_owned_fields(self) -> None:
        first = import_tasks(
            [{"title": "External title", "source": "external", "externalId": "abc", "plan": "later"}]
        )
        task_id = next(task["id"] for task in first["tasks"] if task["externalId"] == "abc")
        update_task(task_id, {"title": "My edited title", "plan": "now", "status": "blocked"})

        repeated = import_tasks(
            [{
                "title": "Changed upstream title",
                "source": "external",
                "externalId": "abc",
                "plan": "today",
                "status": "open",
                "dueDate": "2026-08-10",
            }]
        )
        task = next(task for task in repeated["tasks"] if task["id"] == task_id)
        self.assertEqual(repeated["imported"], 0)
        self.assertEqual(repeated["skipped"], 1)
        self.assertEqual((task["title"], task["plan"], task["status"]), ("My edited title", "now", "blocked"))
        self.assertIsNone(task["dueDate"])

    def test_import_batch_rolls_back_on_invalid_task(self) -> None:
        before = get_board()
        with self.assertRaises(BoardError):
            import_tasks(
                [
                    {"title": "Valid first row", "source": "external", "externalId": "one"},
                    {"title": "Invalid second row", "source": "external"},
                ]
            )
        after = get_board()
        self.assertEqual(after, before)

    def test_import_batch_bumps_revision_once(self) -> None:
        before = get_board()["revision"]
        board = import_tasks(
            [
                {"title": "One", "source": "external", "externalId": "one"},
                {"title": "Two", "source": "external", "externalId": "two"},
            ]
        )
        self.assertEqual(board["revision"], before + 1)

    def test_import_preserves_lifecycle_timestamps_and_existing_legacy_fields(self) -> None:
        board = import_tasks(
            [
                {
                    "id": "done-import",
                    "title": "Imported done task",
                    "status": "done",
                    "createdAt": "2026-07-01T08:00:00Z",
                    "updatedAt": "2026-07-02T09:00:00Z",
                    "completedAt": "2026-07-02T08:30:00Z",
                    "sourcePayload": {
                        "legacyFields": {
                            "kept": "original",
                            "collision": "payload-wins",
                        },
                        "private": True,
                    },
                    "collision": "top-level",
                    "unknownFlag": "retained",
                },
                {
                    "id": "open-import",
                    "title": "Imported open task",
                    "status": "open",
                    "updatedAt": "2026-07-03T10:00:00Z",
                    "completedAt": "2026-07-03T09:00:00Z",
                },
            ]
        )
        by_id = {task["id"]: task for task in board["tasks"]}
        self.assertEqual(by_id["done-import"]["updatedAt"], "2026-07-02T09:00:00Z")
        self.assertEqual(by_id["done-import"]["completedAt"], "2026-07-02T08:30:00Z")
        self.assertEqual(by_id["open-import"]["updatedAt"], "2026-07-03T10:00:00Z")
        self.assertIsNone(by_id["open-import"]["completedAt"])

        conn = sqlite3.connect(resolve_db_path())
        try:
            payload = conn.execute(
                "SELECT source_payload FROM tasks WHERE id = 'done-import'"
            ).fetchone()[0]
        finally:
            conn.close()
        private_payload = json.loads(payload)
        self.assertTrue(private_payload["private"])
        self.assertEqual(
            private_payload["legacyFields"],
            {
                "kept": "original",
                "collision": "payload-wins",
                "unknownFlag": "retained",
            },
        )

    def test_import_rejects_partial_inconsistent_and_duplicate_occurrence_identities(self) -> None:
        invalid = (
            {"title": "Series only", "seriesId": "series-a"},
            {
                "title": "Partial rule identity",
                "dueDate": "2026-08-09",
                "recurrenceRule": "daily",
                "seriesId": "series-a",
            },
            {
                "title": "Inconsistent",
                "seriesId": "series-a",
                "occurrenceId": "series-a:3",
                "occurrenceNumber": 2,
            },
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(BoardError):
                import_tasks([raw])
        self.assertEqual(get_board()["tasks"], [])

        generated = import_tasks(
            [
                {
                    "title": "Rule creates identity",
                    "dueDate": "2026-08-09",
                    "recurrenceRule": "daily",
                }
            ]
        )["tasks"][0]
        self.assertEqual(
            generated["occurrenceId"],
            f"{generated['seriesId']}:{generated['occurrenceNumber']}",
        )

        before = get_board()
        with self.assertRaises(BoardError):
            import_tasks(
                [
                    {
                        "id": "logical-duplicate-one",
                        "title": "Logical duplicate one",
                        "seriesId": "shared-series",
                        "occurrenceId": "shared-series:7",
                        "occurrenceNumber": 7,
                    },
                    {
                        "id": "logical-duplicate-two",
                        "title": "Logical duplicate two",
                        "seriesId": "shared-series",
                        "occurrenceId": "shared-series:7",
                        "occurrenceNumber": 7,
                    },
                ]
            )
        self.assertEqual(get_board(), before)

    def test_timed_due_and_external_metadata_round_trip(self) -> None:
        board = create_task(
            "Timed",
            due_at="2026-08-07T09:30:00+02:00",
            due_timezone="Europe/Amsterdam",
            due_language="en",
            recurrence="every workday",
            source="external",
            external_id="timed-1",
            source_updated_at="2026-08-01T20:00:00Z",
            source_payload={"labels": ["blocked"], "parent_id": "parent"},
        )
        task = board["tasks"][0]
        self.assertIsNone(task["dueDate"])
        self.assertEqual(task["dueAt"], "2026-08-07T09:30:00+02:00")
        self.assertEqual(task["dueTimezone"], "Europe/Amsterdam")
        self.assertEqual(task["dueLanguage"], "en")
        self.assertEqual(task["recurrence"], "every workday")
        self.assertEqual(task["sourceUpdatedAt"], "2026-08-01T20:00:00Z")
        self.assertNotIn("sourcePayload", task)

        conn = sqlite3.connect(resolve_db_path())
        try:
            payload = conn.execute("SELECT source_payload FROM tasks WHERE id = ?", (task["id"],)).fetchone()[0]
            self.assertEqual(payload, '{"labels":["blocked"],"parent_id":"parent"}')
        finally:
            conn.close()

        converted = update_task(task["id"], {"dueDate": "2026-08-08"})
        converted_task = converted["tasks"][0]
        self.assertEqual(converted_task["dueDate"], "2026-08-08")
        self.assertIsNone(converted_task["dueAt"])

    def test_source_payload_has_utf8_byte_limit(self) -> None:
        exact = '"' + ("a" * (MAX_SOURCE_PAYLOAD_BYTES - 2)) + '"'
        board = create_task("Exact payload", source_payload=exact)
        self.assertNotIn("sourcePayload", board["tasks"][0])

        oversized_utf8 = '"' + ("é" * (MAX_SOURCE_PAYLOAD_BYTES // 2)) + '"'
        with self.assertRaisesRegex(BoardError, "UTF-8 bytes"):
            create_task("Oversized payload", source_payload=oversized_utf8)

        task_id = board["tasks"][0]["id"]
        with self.assertRaisesRegex(BoardError, "UTF-8 bytes"):
            update_task(task_id, {"sourcePayload": {"value": "x" * MAX_SOURCE_PAYLOAD_BYTES}})

    def test_import_stores_category_without_legacy_fields(self) -> None:
        board = import_tasks(
            [
                {
                    "id": "cat-import",
                    "title": "Imported tomorrow",
                    "category": "tomorrow",
                    "unknownFlag": "retained",
                }
            ]
        )
        task = next(task for task in board["tasks"] if task["id"] == "cat-import")
        self.assertEqual(task["category"], "tomorrow")

        conn = sqlite3.connect(resolve_db_path())
        try:
            payload = conn.execute(
                "SELECT source_payload FROM tasks WHERE id = 'cat-import'"
            ).fetchone()[0]
        finally:
            conn.close()
        private_payload = json.loads(payload)
        self.assertEqual(private_payload["legacyFields"], {"unknownFlag": "retained"})
        self.assertNotIn("category", private_payload["legacyFields"])

    def test_import_does_not_replace_existing_now(self) -> None:
        existing = create_task("Existing focus", plan="now")
        existing_id = next(task["id"] for task in existing["tasks"] if task["plan"] == "now")
        board = import_tasks([{"id": "legacy-now", "title": "Local focus", "lane": "now"}])
        now_tasks = [task for task in board["tasks"] if task["status"] == "open" and task["plan"] == "now"]
        self.assertEqual([task["id"] for task in now_tasks], [existing_id])
        self.assertEqual(next(task for task in board["tasks"] if task["id"] == "legacy-now")["plan"], "today")

    def test_concurrent_focus_writes_keep_invariant(self) -> None:
        errors: list[Exception] = []
        get_board()  # initialise the schema before exercising concurrent writes

        def worker(index: int) -> None:
            try:
                create_task(f"Task {index}", plan="now")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        board = get_board()
        self.assertEqual(len(board["tasks"]), 8)
        self.assertEqual(
            sum(task["status"] == "open" and task["plan"] == "now" for task in board["tasks"]),
            1,
        )

    def test_rejects_invalid_dimensions_and_metadata(self) -> None:
        with self.assertRaises(BoardError):
            create_task("Bad plan", plan="someday")
        with self.assertRaises(BoardError):
            create_task("Bad status", status="stuck")
        with self.assertRaises(BoardError):
            create_task("Bad due", due_date="Fridayish")
        with self.assertRaises(BoardError):
            create_task("Half identity", source="external")
        with self.assertRaises(BoardError):
            create_task("Bad priority", priority=5)
        with self.assertRaisesRegex(BoardError, "valid IANA timezone"):
            create_task("Bad timezone", recurrence_timezone="../x")

    def test_store_boundaries_reject_coercible_or_malformed_values(self) -> None:
        invalid_creates = (
            {"title": True},
            {"title": 123},
            {"title": "Bad estimate", "estimate": True},
            {"title": "Bad estimate", "estimate": "25"},
            {"title": "Bad estimate", "estimate": 25.0},
            {"title": "Bad priority", "priority": False},
            {"title": "Bad priority", "priority": "2"},
            {"title": "Bad plan", "plan": 1},
            {"title": "Bad status", "status": True},
            {"title": "Bad lane", "lane": []},
            {"title": "Bad lane", "lane": "someday"},
            {"title": "Bad project", "project": {"name": "Work"}},
            {"title": "Bad due date", "due_date": 20260807},
            {"title": "Bad metadata", "source_updated_at": False},
        )
        for kwargs in invalid_creates:
            with self.subTest(kwargs=kwargs), self.assertRaises(BoardError):
                create_task(**kwargs)

        task = create_task("Valid")["tasks"][0]
        for field, value in (("estimate", False), ("priority", "3"), ("project", 7)):
            with self.subTest(field=field), self.assertRaises(BoardError):
                update_task(task["id"], {field: value})

        with self.assertRaises(BoardError):
            import_tasks([{"title": "Numeric estimate", "estimate": "25"}])

    def test_additive_v3_migration_preserves_unknown_columns_and_adds_safe_defaults(self) -> None:
        path = resolve_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE board_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            INSERT INTO board_meta(key, value) VALUES ('revision', 9);
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL,
                estimate INTEGER NOT NULL,
                due_date TEXT,
                due_at TEXT,
                due_timezone TEXT,
                due_language TEXT,
                source TEXT,
                external_id TEXT,
                project TEXT,
                priority INTEGER,
                recurrence TEXT,
                source_updated_at TEXT,
                source_payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                future_field TEXT
            );
            INSERT INTO tasks VALUES (
                'legacy-v3', 'Legacy v3', 'later', 'open', 25,
                NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                'every time it rains', NULL, NULL,
                '2026-07-01T09:00:00Z', '2026-07-01T09:00:00Z', NULL,
                'preserve me'
            );
            PRAGMA user_version = 3;
            """
        )
        conn.commit()
        conn.close()

        board = get_board()
        task = board["tasks"][0]
        self.assertEqual(board["revision"], 9)
        self.assertEqual(task["recurrence"], "every time it rains")
        self.assertIsNone(task["recurrenceRule"])
        self.assertEqual((task["executionMode"], task["approvalState"], task["inbox"]), ("manual", "not-required", False))

        conn = sqlite3.connect(path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
            self.assertIn("future_field", columns)
            self.assertEqual(
                conn.execute("SELECT future_field FROM tasks WHERE id = 'legacy-v3'").fetchone()[0],
                "preserve me",
            )
        finally:
            conn.close()

    def test_optional_occurrence_index_does_not_block_existing_v4_database(self) -> None:
        path = resolve_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE board_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO board_meta(key, value) VALUES ('revision', 2)")
        store._create_v4_tasks(conn)
        for task_id in ("existing-one", "existing-two"):
            conn.execute(
                """
                INSERT INTO tasks(
                    id, title, plan, status, estimate, occurrence_id,
                    created_at, updated_at
                ) VALUES (?, ?, 'today', 'open', 25, 'legacy-series:1', ?, ?)
                """,
                (task_id, task_id, "2026-07-01T09:00:00Z", "2026-07-01T09:00:00Z"),
            )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()

        board = get_board()
        self.assertEqual(len(board["tasks"]), 2)
        conn = sqlite3.connect(path)
        try:
            occurrence_index = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_tasks_v4_occurrence'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNone(occurrence_index)

    def test_lifecycle_context_round_trips_and_history_is_machine_readable(self) -> None:
        board = create_task(
            "Ship the slice",
            brief="Decision: keep SQLite authoritative.",
            next_action="Add the contract tests",
            closure_condition="Full suite passes",
            waiting_on="Reviewer",
            review_date="2026-08-11",
            blocker="Need a fixture",
            artefacts=["README.md", "https://example.invalid/proof"],
            owner="Dan",
            execution_mode="supervised",
            approval_state="pending",
        )
        task = board["tasks"][0]
        self.assertEqual(task["nextAction"], "Add the contract tests")
        self.assertEqual(task["artefacts"], ["README.md", "https://example.invalid/proof"])
        self.assertEqual((task["owner"], task["executionMode"], task["approvalState"]), ("Dan", "supervised", "pending"))

        update_task(
            task["id"],
            {"title": "Ship Hermes Todo v0.2", "artefacts": [*task["artefacts"], "tests/test_store.py"]},
        )
        history = get_history(task["id"])["events"]
        event_types = [event["type"] for event in history]
        self.assertIn("task.created", event_types)
        self.assertIn("task.retitled", event_types)
        self.assertIn("artefact.attached", event_types)
        self.assertTrue(all(isinstance(event["data"], dict) for event in history))

        with self.assertRaisesRegex(BoardError, "at most"):
            create_task("Too many", artefacts=[str(index) for index in range(21)])
        with self.assertRaisesRegex(BoardError, "execution mode"):
            create_task("Bad mode", execution_mode="magic")

    def test_inbox_capture_does_not_claim_now_and_start_is_explicit(self) -> None:
        captured = create_task("Captured thought", plan="later", inbox=True)
        task = captured["tasks"][0]
        self.assertEqual((task["plan"], task["inbox"]), ("later", True))
        self.assertEqual(
            [item for item in captured["tasks"] if item["status"] == "open" and item["plan"] == "now"],
            [],
        )
        started = update_task(task["id"], {"plan": "now", "status": "open", "inbox": False})
        started_task = next(item for item in started["tasks"] if item["id"] == task["id"])
        self.assertEqual((started_task["plan"], started_task["inbox"]), ("now", False))

    def test_agenda_is_bounded_explains_reasons_and_does_not_rewrite_plan(self) -> None:
        captured = create_task("Triage me", plan="later", inbox=True)["tasks"][0]
        create_task("Current focus", plan="now")
        create_task("Due today", plan="later", due_date="2026-08-09")
        create_task(
            "Future waiting review",
            plan="later",
            status="waiting",
            waiting_on="CI",
            review_date="2026-08-10",
        )
        create_task(
            "Waiting review today",
            plan="later",
            status="waiting",
            waiting_on="CI",
            review_date="2026-08-09",
        )
        create_task("Blocked", plan="today", status="blocked", blocker="Approval")
        stale_board = create_task("Stale", plan="later")
        stale = next(task for task in stale_board["tasks"] if task["title"] == "Stale")
        conn = sqlite3.connect(resolve_db_path())
        try:
            conn.execute("UPDATE tasks SET updated_at = '2026-07-01T00:00:00Z' WHERE id = ?", (stale["id"],))
            conn.commit()
        finally:
            conn.close()

        agenda = get_agenda(on_date="2026-08-09", timezone_name="Europe/Amsterdam", limit=20)
        reasons = {
            item["task"]["title"]: {reason["code"] for reason in item["reasons"]}
            for item in agenda["items"]
        }
        self.assertIn("inbox", reasons["Triage me"])
        self.assertIn("now", reasons["Current focus"])
        self.assertIn("due_today", reasons["Due today"])
        self.assertNotIn("Future waiting review", reasons)
        self.assertIn("waiting_review", reasons["Waiting review today"])
        self.assertIn("blocked", reasons["Blocked"])
        self.assertIn("stale", reasons["Stale"])
        self.assertEqual(
            next(task for task in get_board()["tasks"] if task["id"] == captured["id"])["plan"],
            "later",
        )
        self.assertTrue(get_agenda(on_date="2026-08-09", limit=1)["truncated"])

    def test_agenda_uses_due_timezone_for_naive_deadlines_with_safe_fallback(self) -> None:
        create_task(
            "Amsterdam midnight",
            plan="later",
            due_at="2026-08-10T00:30:00",
            due_timezone="Europe/Amsterdam",
        )
        create_task(
            "Invalid legacy timezone",
            plan="later",
            due_at="2026-08-09T09:00:00",
            due_timezone="Invalid/Legacy",
        )

        agenda = get_agenda(
            on_date="2026-08-09",
            timezone_name="America/Los_Angeles",
            limit=20,
        )
        reasons = {
            item["task"]["title"]: {reason["code"] for reason in item["reasons"]}
            for item in agenda["items"]
        }
        self.assertIn("due_today", reasons["Amsterdam midnight"])
        self.assertIn("due_today", reasons["Invalid legacy timezone"])

    def test_revision_conflict_preserves_both_writers(self) -> None:
        created = create_task("Concurrent", return_board=False, expected_revision=0)
        task_id = created["task"]["id"]
        first = update_task(
            task_id,
            {"brief": "Writer one"},
            expected_revision=created["revision"],
            return_board=False,
        )
        with self.assertRaises(RevisionConflict) as caught:
            update_task(
                task_id,
                {"nextAction": "Writer two"},
                expected_revision=created["revision"],
                return_board=False,
            )
        self.assertEqual(caught.exception.current_revision, first["revision"])
        task = get_board()["tasks"][0]
        self.assertEqual(task["brief"], "Writer one")
        self.assertIsNone(task["nextAction"])

    def test_recurrence_generates_stable_idempotent_occurrences(self) -> None:
        first_board = create_task(
            "Month end",
            due_date="2026-08-31",
            recurrence="legacy display note",
            recurrence_rule="monthly",
            recurrence_timezone="Europe/Amsterdam",
        )
        first = first_board["tasks"][0]
        completed = update_task(first["id"], {"status": "done", "closureNote": "August closed"})
        by_number = {task["occurrenceNumber"]: task for task in completed["tasks"]}
        self.assertEqual(set(by_number), {1, 2})
        self.assertEqual(by_number[2]["dueDate"], "2026-09-30")
        self.assertEqual(by_number[1]["seriesId"], by_number[2]["seriesId"])
        self.assertEqual(by_number[2]["occurrenceId"], f"{by_number[1]['seriesId']}:2")
        self.assertEqual(completed["generatedTask"]["id"], by_number[2]["id"])

        repeated = generate_next_occurrence(first["id"])
        self.assertFalse(repeated["created"])
        self.assertEqual(repeated["generatedTask"]["id"], by_number[2]["id"])
        recurrence_events = [
            event for event in get_history(first["id"])["events"]
            if event["type"] == "recurrence.generated"
        ]
        self.assertEqual(len(recurrence_events), 1)

        legacy = create_task("Legacy prose", due_date="2026-08-09", recurrence="whenever useful")
        legacy_id = next(task["id"] for task in legacy["tasks"] if task["title"] == "Legacy prose")
        after_legacy_done = update_task(legacy_id, {"status": "done"})
        self.assertEqual(sum(task["title"] == "Legacy prose" for task in after_legacy_done["tasks"]), 1)

    def test_timed_recurrence_preserves_local_clock_across_dst(self) -> None:
        first = create_task(
            "Weekly local check",
            due_at="2026-03-28T09:00:00+01:00",
            due_timezone="Europe/Amsterdam",
            recurrence_rule="weekly",
            recurrence_timezone="Europe/Amsterdam",
        )["tasks"][0]
        completed = update_task(first["id"], {"status": "done"})
        next_task = next(task for task in completed["tasks"] if task["occurrenceNumber"] == 2)
        self.assertEqual(next_task["dueAt"], "2026-04-04T09:00:00+02:00")

    def test_timed_recurrence_normalises_nonexistent_dst_time(self) -> None:
        first = create_task(
            "Weekly DST gap check",
            due_at="2026-03-22T02:30:00+01:00",
            due_timezone="Europe/Amsterdam",
            recurrence_rule="weekly",
            recurrence_timezone="Europe/Amsterdam",
        )["tasks"][0]
        completed = update_task(first["id"], {"status": "done"})
        next_task = next(task for task in completed["tasks"] if task["occurrenceNumber"] == 2)
        self.assertEqual(next_task["dueAt"], "2026-03-29T03:30:00+02:00")

    def test_session_link_is_reused_and_blocked_work_requires_explicit_clear(self) -> None:
        task = create_task("Agent work", plan="later", execution_mode="supervised")["tasks"][0]
        linked = link_task_session(task["id"], "stored-session-1", expected_revision=1)
        self.assertEqual((linked["task"]["plan"], linked["task"]["sessionState"]), ("now", "active"))
        revision = linked["revision"]
        repeated = link_task_session(task["id"], "stored-session-1", expected_revision=revision)
        self.assertEqual(repeated["revision"], revision)
        with self.assertRaisesRegex(BoardError, "active linked session"):
            link_task_session(task["id"], "stored-session-2")
        finished = complete_task_session(task["id"], expected_revision=revision)
        self.assertEqual(finished["task"]["sessionState"], "completed")

        blocked = create_task("Blocked work", status="blocked", blocker="Need approval")["tasks"][-1]
        with self.assertRaisesRegex(BoardError, "Reopen"):
            link_task_session(blocked["id"], "stored-session-3")
        with self.assertRaisesRegex(BoardError, "Clear the blocker"):
            update_task(blocked["id"], {"status": "open"})
        reopened = update_task(blocked["id"], {"status": "open", "blocker": None})
        reopened_task = next(task for task in reopened["tasks"] if task["id"] == blocked["id"])
        self.assertEqual((reopened_task["status"], reopened_task["blocker"]), ("open", None))

    def test_complete_with_follow_up_is_atomic(self) -> None:
        parent = create_task("Send request")["tasks"][0]
        result = complete_with_follow_up(
            parent["id"],
            {
                "title": "Check response",
                "plan": "later",
                "status": "waiting",
                "waitingOn": "Reviewer",
                "reviewDate": "2026-08-12",
            },
            closure_note="Request sent locally; delivery unverified",
            closure_evidence=["drafts/request.md"],
            expected_revision=1,
        )
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["task"]["status"], "done")
        self.assertEqual(result["followUpTask"]["status"], "waiting")
        self.assertEqual(result["followUpTask"]["waitingOn"], "Reviewer")
        self.assertEqual(len(get_board()["tasks"]), 2)

    def test_complete_with_follow_up_preserves_omitted_closure_and_allows_clearing(self) -> None:
        preserved_parent = create_task(
            "Preserve closure",
            closure_note="Existing note",
            closure_evidence=["existing-proof.txt"],
        )["tasks"][0]
        preserved = complete_with_follow_up(
            preserved_parent["id"],
            {"title": "Preserved follow-up"},
        )
        self.assertEqual(preserved["task"]["closureNote"], "Existing note")
        self.assertEqual(preserved["task"]["closureEvidence"], ["existing-proof.txt"])

        cleared_board = create_task(
            "Clear closure",
            closure_note="Clear this note",
            closure_evidence=["clear-this-proof.txt"],
        )
        cleared_parent = next(
            task for task in cleared_board["tasks"] if task["title"] == "Clear closure"
        )
        cleared = complete_with_follow_up(
            cleared_parent["id"],
            {"title": "Cleared follow-up"},
            closure_note=None,
            closure_evidence=[],
        )
        self.assertIsNone(cleared["task"]["closureNote"])
        self.assertEqual(cleared["task"]["closureEvidence"], [])

    def test_search_reads_context_without_exposing_source_payload(self) -> None:
        create_task(
            "Implement contract",
            brief="Optimistic concurrency decision",
            owner="Dan",
            source_payload={"private": "not returned"},
        )
        result = search_tasks("concurrency", owner="dan")
        self.assertEqual(result["total"], 1)
        self.assertNotIn("sourcePayload", result["tasks"][0])


if __name__ == "__main__":
    unittest.main()
