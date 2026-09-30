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

    def test_create_update_and_compact_keep_artefacts(self) -> None:
        created = self._call(
            action="create",
            title="Ship with proof",
            artefacts=["tests/test_todo_tool.py"],
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["task"]["artefacts"], ["tests/test_todo_tool.py"])
        self.assertIn("artefacts", todo_tool.SCHEMA["parameters"]["properties"])

        updated = self._call(
            action="update",
            task_id=created["task"]["id"],
            artefacts=["tests/test_todo_tool.py", "README.md"],
        )
        self.assertTrue(updated["ok"])
        self.assertEqual(
            updated["task"]["artefacts"],
            ["tests/test_todo_tool.py", "README.md"],
        )

        fetched = self._call(action="get", task_id=created["task"]["id"])
        self.assertTrue(fetched["ok"])
        self.assertEqual(
            fetched["task"]["artefacts"],
            ["tests/test_todo_tool.py", "README.md"],
        )


if __name__ == "__main__":
    unittest.main()
