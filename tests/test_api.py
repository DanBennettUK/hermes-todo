from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from dashboard.plugin_api import TaskCreate, TaskPatch, patch_task
import hermes_todo_store as store
from hermes_todo_store import create_task, get_board


class HermesTodoApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_home = os.environ.get("HERMES_HOME")
        self._old_home_resolver = store._get_hermes_home
        store._get_hermes_home = None
        self._temp = tempfile.TemporaryDirectory()
        os.environ["HERMES_HOME"] = self._temp.name

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._old_home
        store._get_hermes_home = self._old_home_resolver
        self._temp.cleanup()

    def test_patch_distinguishes_omitted_field_from_explicit_null(self) -> None:
        board = create_task("Deadline", due_date="2026-08-03")
        task_id = board["tasks"][0]["id"]

        omitted = TaskPatch()
        self.assertEqual(omitted.model_dump(exclude_unset=True), {})
        unchanged = patch_task(task_id, omitted)
        self.assertEqual(unchanged["tasks"][0]["dueDate"], "2026-08-03")

        cleared = TaskPatch(dueDate=None)
        self.assertEqual(cleared.model_dump(exclude_unset=True), {"due_date": None})
        result = patch_task(task_id, cleared)
        self.assertIsNone(result["tasks"][0]["dueDate"])

    def test_date_only_patch_explicitly_clears_timed_deadline(self) -> None:
        board = create_task(
            "Timed",
            due_at="2026-08-03T09:30:00+02:00",
            due_timezone="Europe/Amsterdam",
        )
        task_id = board["tasks"][0]["id"]
        result = patch_task(
            task_id,
            TaskPatch(dueDate="2026-08-04", dueAt=None, dueTimezone=None),
        )
        task = result["tasks"][0]
        self.assertEqual(task["dueDate"], "2026-08-04")
        self.assertIsNone(task["dueAt"])
        self.assertIsNone(task["dueTimezone"])

    def test_api_models_do_not_coerce_boolean_or_text_numbers(self) -> None:
        invalid_models = (
            (TaskCreate, {"title": "Strict", "priority": True}),
            (TaskCreate, {"title": "Strict", "priority": "2"}),
            (TaskCreate, {"title": "Strict", "estimate": "25"}),
            (TaskPatch, {"priority": True}),
            (TaskPatch, {"priority": "2"}),
            (TaskPatch, {"estimate": "25"}),
        )
        for model, payload in invalid_models:
            with self.subTest(model=model.__name__, payload=payload):
                with self.assertRaises(ValidationError):
                    model(**payload)


if __name__ == "__main__":
    unittest.main()
