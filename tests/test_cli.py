from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from cli import register_cli
import hermes_todo_store as store


class HermesTodoCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_home = os.environ.get("HERMES_HOME")
        self.old_home_resolver = store._get_hermes_home
        store._get_hermes_home = None
        self.temp = tempfile.TemporaryDirectory()
        os.environ["HERMES_HOME"] = self.temp.name

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self.old_home
        store._get_hermes_home = self.old_home_resolver
        self.temp.cleanup()

    def run_cli(self, argv: list[str]) -> tuple[int, dict]:
        parser = argparse.ArgumentParser()
        register_cli(parser)
        args = parser.parse_args(argv)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = args.func(args)
        return code, json.loads(output.getvalue())

    def test_add_list_and_focus(self) -> None:
        code, added = self.run_cli(
            ["add", "Write docs", "--plan", "later", "--priority", "2", "--compact"]
        )
        self.assertEqual(code, 0)
        task = added["task"]
        self.assertEqual((task["plan"], task["priority"]), ("later", 2))
        code, focused = self.run_cli(["focus", task["id"], "--compact"])
        self.assertEqual(code, 0)
        self.assertEqual(focused["task"]["plan"], "now")

    def test_json_import_is_idempotent(self) -> None:
        path = Path(self.temp.name) / "tasks.json"
        path.write_text(json.dumps({"tasks": [{"title": "Imported", "source": "sample", "externalId": "1"}]}))
        _, first = self.run_cli(["import", str(path), "--compact"])
        _, second = self.run_cli(["import", str(path), "--compact"])
        self.assertEqual((first["revision"], first["imported"], len(first["tasks"])), (1, 1, 1))
        self.assertEqual((second["revision"], second["skipped"], len(second["tasks"])), (1, 1, 0))

    def test_due_and_update_distinguish_dates_datetimes_and_clear(self) -> None:
        _, added = self.run_cli(["add", "Deadline", "--compact"])
        task_id = added["task"]["id"]

        _, dated = self.run_cli(["due", task_id, "2026-08-07", "--compact"])
        self.assertEqual(dated["task"]["dueDate"], "2026-08-07")
        self.assertIsNone(dated["task"]["dueAt"])

        _, timed = self.run_cli(
            ["due", task_id, "2026-08-07T09:30:00+02:00", "--compact"]
        )
        self.assertIsNone(timed["task"]["dueDate"])
        self.assertEqual(timed["task"]["dueAt"], "2026-08-07T09:30:00+02:00")

        _, updated_date = self.run_cli(
            ["update", task_id, "--due", "2026-08-08", "--compact"]
        )
        self.assertEqual(updated_date["task"]["dueDate"], "2026-08-08")
        self.assertIsNone(updated_date["task"]["dueAt"])

        _, updated_time = self.run_cli(
            ["update", task_id, "--due", "2026-08-08T14:00:00Z", "--compact"]
        )
        self.assertIsNone(updated_time["task"]["dueDate"])
        self.assertEqual(updated_time["task"]["dueAt"], "2026-08-08T14:00:00Z")

        _, cleared = self.run_cli(
            ["update", task_id, "--due", "clear", "--compact"]
        )
        self.assertIsNone(cleared["task"]["dueDate"])
        self.assertIsNone(cleared["task"]["dueAt"])

    def test_capture_start_here_show_search_and_history(self) -> None:
        code, captured = self.run_cli(
            [
                "capture",
                "Review contract",
                "--brief",
                "Decisions live here",
                "--owner",
                "Dan",
                "--compact",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual((captured["task"]["plan"], captured["task"]["inbox"]), ("later", True))
        task_id = captured["task"]["id"]

        _, agenda = self.run_cli(["agenda", "--date", "2026-08-09"])
        self.assertEqual(agenda["items"][0]["reasons"][0]["code"], "inbox")
        _, shown = self.run_cli(["show", task_id, "--history"])
        self.assertEqual(shown["task"]["brief"], "Decisions live here")
        self.assertEqual(shown["history"][0]["type"], "task.created")
        _, searched = self.run_cli(["search", "decisions", "--owner", "dan"])
        self.assertEqual(searched["total"], 1)

        _, started = self.run_cli(
            [
                "start",
                task_id,
                "--expected-revision",
                str(captured["revision"]),
                "--compact",
            ]
        )
        self.assertEqual((started["task"]["plan"], started["task"]["inbox"]), ("now", False))

    def test_conflict_is_explicit_and_board_envelope_is_the_default(self) -> None:
        _, added = self.run_cli(["add", "Concurrent"])
        self.assertEqual(len(added["tasks"]), 1)
        task_id = added["tasks"][0]["id"]
        _, first = self.run_cli(
            ["update", task_id, "--brief", "first", "--expected-revision", "1"]
        )
        self.assertEqual(first["revision"], 2)
        code, conflict = self.run_cli(
            ["update", task_id, "--next-action", "second", "--expected-revision", "1"]
        )
        self.assertEqual(code, 3)
        self.assertEqual(conflict["error"], "revision_conflict")
        self.assertEqual(conflict["currentRevision"], 2)

        _, explicit_board = self.run_cli(["add", "Explicit board", "--board"])
        self.assertEqual(len(explicit_board["tasks"]), 2)
        _, compact = self.run_cli(["add", "Compact result", "--compact"])
        self.assertEqual(compact["task"]["title"], "Compact result")

    def test_board_envelope_keeps_recurrence_and_follow_up_results(self) -> None:
        _, recurring = self.run_cli(
            [
                "add",
                "Recurring CLI task",
                "--due",
                "2026-08-09",
                "--recurrence-rule",
                "daily",
            ]
        )
        recurring_id = recurring["tasks"][0]["id"]
        _, completed = self.run_cli(["done", recurring_id])
        self.assertEqual(len(completed["tasks"]), 2)
        self.assertIn("generatedTask", completed)

        parent_board = store.create_task("CLI follow-up parent")
        parent = next(
            task for task in parent_board["tasks"] if task["title"] == "CLI follow-up parent"
        )
        _, followed = self.run_cli(["follow-up", parent["id"], "CLI follow-up child"])
        self.assertIn("followUpTask", followed)
        self.assertIn(
            followed["followUpTask"]["id"],
            {task["id"] for task in followed["tasks"]},
        )

    def test_follow_up_closure_flags_distinguish_omitted_from_explicit_empty(self) -> None:
        preserved_parent = store.create_task(
            "CLI preserve closure",
            closure_note="Existing CLI note",
            closure_evidence=["existing-cli-proof.txt"],
        )["tasks"][-1]
        _, preserved = self.run_cli(
            ["follow-up", preserved_parent["id"], "CLI preserved child", "--compact"]
        )
        self.assertEqual(preserved["task"]["closureNote"], "Existing CLI note")
        self.assertEqual(
            preserved["task"]["closureEvidence"], ["existing-cli-proof.txt"]
        )

        cleared_board = store.create_task(
            "CLI clear closure",
            closure_note="Clear CLI note",
            closure_evidence=["clear-cli-proof.txt"],
        )
        cleared_parent = next(
            task for task in cleared_board["tasks"] if task["title"] == "CLI clear closure"
        )
        _, cleared = self.run_cli(
            [
                "follow-up",
                cleared_parent["id"],
                "CLI cleared child",
                "--closure-note",
                "",
                "--evidence",
                "",
                "--compact",
            ]
        )
        self.assertIsNone(cleared["task"]["closureNote"])
        self.assertEqual(cleared["task"]["closureEvidence"], [])

    def test_empty_board_read_contracts_are_stable(self) -> None:
        _, listed = self.run_cli(["list"])
        self.assertEqual((listed["total"], listed["tasks"]), (0, []))
        _, agenda = self.run_cli(["agenda", "--date", "2026-08-09"])
        self.assertEqual((agenda["total"], agenda["items"], agenda["truncated"]), (0, [], False))


if __name__ == "__main__":
    unittest.main()
