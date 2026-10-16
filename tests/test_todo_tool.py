from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN_ROOT))

import hermes_todo_store as store
import todo_tool
from hermes_todo_store import create_task, get_board

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "server-plugin" / "hermes-todo"


class HermesTodoBoardToolTests(unittest.TestCase):
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

    def _call(self, module=todo_tool, **args):
        return json.loads(module.todo_board(args))

    def test_package_import_loads_the_store(self) -> None:
        source = (PACKAGE_DIR / "todo_tool.py").read_text(encoding="utf-8")
        self.assertIn("from .hermes_todo_store import", source)
        pkg_name = "hermes_todo_plugin"
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(PACKAGE_DIR)]
        pkg.__package__ = pkg_name
        sys.modules[pkg_name] = pkg
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.todo_tool",
            PACKAGE_DIR / "todo_tool.py",
            submodule_search_locations=[str(PACKAGE_DIR)],
        )
        module = importlib.util.module_from_spec(spec)
        module.__package__ = pkg_name
        sys.modules[f"{pkg_name}.todo_tool"] = module
        spec.loader.exec_module(module)
        created = self._call(module, action="create", title="From package import")
        self.assertTrue(created["ok"])
        self.assertEqual(created["task"]["title"], "From package import")

    def test_stale_revision_rejects_create_update_reorder_start_and_done(self) -> None:
        first = create_task("Guard me", return_board=False)["task"]
        second = create_task("Neighbour", return_board=False)["task"]
        revision = get_board()["revision"]
        stale = revision - 1

        created = self._call(action="create", title="Too late", expected_revision=stale)
        self.assertFalse(created["ok"])
        self.assertEqual(created["error"], "revision_conflict")

        updated = self._call(
            action="update",
            task_id=first["id"],
            title="Still stale",
            expected_revision=stale,
        )
        self.assertFalse(updated["ok"])
        self.assertEqual(updated["error"], "revision_conflict")

        reordered = self._call(
            action="reorder",
            task_id=first["id"],
            after_id=second["id"],
            expected_revision=stale,
        )
        self.assertFalse(reordered["ok"])
        self.assertEqual(reordered["error"], "revision_conflict")

        started = self._call(action="start", task_id=first["id"], expected_revision=stale)
        self.assertFalse(started["ok"])
        self.assertEqual(started["error"], "revision_conflict")

        done = self._call(action="done", task_id=first["id"], expected_revision=stale)
        self.assertFalse(done["ok"])
        self.assertEqual(done["error"], "revision_conflict")

        current = get_board()
        by_id = {task["id"]: task for task in current["tasks"]}
        self.assertEqual(by_id[first["id"]]["title"], "Guard me")
        self.assertEqual(by_id[first["id"]]["status"], "open")
        self.assertNotEqual(by_id[first["id"]]["plan"], "now")

    def test_unknown_action_is_named(self) -> None:
        missing = self._call(action="bogus")
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"], "invalid_request")
        self.assertEqual(missing["message"], "Unknown action: bogus")

        supplied = self._call(action="bogus", task_id="task-1")
        self.assertEqual(supplied["message"], "Unknown action: bogus")

        update = self._call(action="update")
        self.assertEqual(update["message"], "update requires task_id")

    def test_list_returns_only_the_requested_category(self) -> None:
        self._call(action="create", title="Today item", category="today")
        self._call(action="create", title="Soon item", category="soon")
        listed = self._call(action="list", category="soon")
        self.assertTrue(listed["ok"])
        self.assertEqual([task["title"] for task in listed["tasks"]], ["Soon item"])
        self.assertEqual(listed["total"], 1)

    def test_get_returns_the_task_and_its_history(self) -> None:
        created = self._call(action="create", title="Readable")
        got = self._call(action="get", task_id=created["task"]["id"], history_limit=5)
        self.assertTrue(got["ok"])
        self.assertEqual(got["task"]["title"], "Readable")
        self.assertIn("task.created", [event["type"] for event in got["history"]])

    def test_create_stores_the_requested_task(self) -> None:
        created = self._call(
            action="create",
            title="Draft notes",
            category="tomorrow",
            priority=2,
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["task"]["title"], "Draft notes")
        self.assertEqual(created["task"]["category"], "tomorrow")
        self.assertEqual(created["task"]["priority"], 2)
        listed = self._call(action="list", category="tomorrow")
        self.assertEqual(listed["tasks"][0]["id"], created["task"]["id"])

    def test_update_changes_title_category_and_priority(self) -> None:
        created = self._call(action="create", title="Old title", category="today")
        updated = self._call(
            action="update",
            task_id=created["task"]["id"],
            title="New title",
            category="this-week",
            priority=1,
        )
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["task"]["title"], "New title")
        self.assertEqual(updated["task"]["category"], "this-week")
        self.assertEqual(updated["task"]["priority"], 1)

    def test_reorder_moves_the_task_into_the_named_category(self) -> None:
        created = self._call(action="create", title="Movable", category="today")
        moved = self._call(
            action="reorder",
            task_id=created["task"]["id"],
            category="this-month",
        )
        self.assertTrue(moved["ok"])
        self.assertEqual(moved["task"]["category"], "this-month")
        listed = self._call(action="list", category="today")
        self.assertEqual(listed["tasks"], [])

    def test_start_makes_the_task_the_open_now_item(self) -> None:
        created = self._call(action="create", title="Focus", plan="later", category="soon")
        started = self._call(action="start", task_id=created["task"]["id"])
        self.assertTrue(started["ok"])
        self.assertEqual(started["task"]["plan"], "now")
        self.assertEqual(started["task"]["status"], "open")

    def test_done_marks_the_task_complete(self) -> None:
        created = self._call(action="create", title="Finish me")
        finished = self._call(
            action="done",
            task_id=created["task"]["id"],
            closure_note="Checked the board",
        )
        self.assertTrue(finished["ok"])
        self.assertEqual(finished["task"]["status"], "done")
        got = self._call(action="get", task_id=created["task"]["id"])
        self.assertEqual(got["task"]["status"], "done")

    def test_subtask_create_adds_a_checklist_item(self) -> None:
        parent = self._call(action="create", title="Parent")
        created = self._call(
            action="subtask_create",
            task_id=parent["task"]["id"],
            subtask_title="Write notes",
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["subtask"]["title"], "Write notes")
        self.assertFalse(created["subtask"]["done"])
        self.assertEqual(created["task"]["subtasks"], [created["subtask"]])

    def test_subtask_update_marks_the_item_done(self) -> None:
        parent = self._call(action="create", title="Parent")
        created = self._call(
            action="subtask_create",
            task_id=parent["task"]["id"],
            subtask_title="Write notes",
        )
        updated = self._call(
            action="subtask_update",
            task_id=parent["task"]["id"],
            subtask_id=created["subtask"]["id"],
            done=True,
        )
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["task"]["subtasks"][0]["done"], True)

    def test_subtask_delete_removes_the_item(self) -> None:
        parent = self._call(action="create", title="Parent")
        created = self._call(
            action="subtask_create",
            task_id=parent["task"]["id"],
            subtask_title="Write notes",
        )
        deleted = self._call(
            action="subtask_delete",
            task_id=parent["task"]["id"],
            subtask_id=created["subtask"]["id"],
        )
        self.assertTrue(deleted["ok"])
        self.assertNotIn("subtasks", deleted["task"])
        got = self._call(action="get", task_id=parent["task"]["id"])
        self.assertNotIn("subtasks", got["task"])

    def test_subtask_reorder_places_the_item_after_its_neighbour(self) -> None:
        parent = self._call(action="create", title="Parent")
        first = self._call(
            action="subtask_create",
            task_id=parent["task"]["id"],
            subtask_title="First",
        )
        second = self._call(
            action="subtask_create",
            task_id=parent["task"]["id"],
            subtask_title="Second",
        )
        reordered = self._call(
            action="subtask_reorder",
            task_id=parent["task"]["id"],
            subtask_id=first["subtask"]["id"],
            after_id=second["subtask"]["id"],
        )
        self.assertTrue(reordered["ok"])
        self.assertEqual(
            [item["title"] for item in reordered["task"]["subtasks"]],
            ["Second", "First"],
        )

    def test_subtask_sort_puts_open_items_before_completed_ones(self) -> None:
        parent = self._call(action="create", title="Parent")
        done_item = self._call(
            action="subtask_create",
            task_id=parent["task"]["id"],
            subtask_title="Already done",
            done=True,
        )
        self._call(
            action="subtask_create",
            task_id=parent["task"]["id"],
            subtask_title="Still open",
        )
        self.assertEqual(done_item["subtask"]["done"], True)
        sorted_items = self._call(action="subtask_sort", task_id=parent["task"]["id"])
        self.assertTrue(sorted_items["ok"])
        self.assertEqual(
            [(item["title"], item["done"]) for item in sorted_items["task"]["subtasks"]],
            [("Still open", False), ("Already done", True)],
        )


if __name__ == "__main__":
    unittest.main()
