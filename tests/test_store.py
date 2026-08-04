from __future__ import annotations

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
    create_task,
    get_board,
    import_tasks,
    resolve_db_path,
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
        self.assertEqual(board["version"], 3)
        self.assertEqual(board["revision"], 7)
        self.assertEqual(len(board["tasks"]), 3)
        by_id = {task["id"]: task for task in board["tasks"]}
        self.assertEqual((by_id["n"]["plan"], by_id["n"]["status"]), ("now", "open"))
        self.assertEqual(by_id["w"]["status"], "waiting")
        self.assertEqual(by_id["d"]["status"], "done")
        self.assertIsNone(by_id["n"]["dueDate"])
        self.assertIsNone(by_id["n"]["dueAt"])
        conn = sqlite3.connect(resolve_db_path())
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
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


if __name__ == "__main__":
    unittest.main()
