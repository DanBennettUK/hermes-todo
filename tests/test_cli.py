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
        code, added = self.run_cli(["add", "Write docs", "--plan", "later", "--priority", "2"])
        self.assertEqual(code, 0)
        task = added["tasks"][0]
        self.assertEqual((task["plan"], task["priority"]), ("later", 2))
        code, focused = self.run_cli(["focus", task["id"]])
        self.assertEqual(code, 0)
        self.assertEqual(focused["tasks"][0]["plan"], "now")

    def test_json_import_is_idempotent(self) -> None:
        path = Path(self.temp.name) / "tasks.json"
        path.write_text(json.dumps({"tasks": [{"title": "Imported", "source": "sample", "externalId": "1"}]}))
        _, first = self.run_cli(["import", str(path)])
        _, second = self.run_cli(["import", str(path)])
        self.assertEqual((first["revision"], len(first["tasks"])), (1, 1))
        self.assertEqual((second["revision"], len(second["tasks"])), (1, 1))

    def test_due_and_update_distinguish_dates_datetimes_and_clear(self) -> None:
        _, added = self.run_cli(["add", "Deadline"])
        task_id = added["tasks"][0]["id"]

        _, dated = self.run_cli(["due", task_id, "2026-08-07"])
        self.assertEqual(dated["tasks"][0]["dueDate"], "2026-08-07")
        self.assertIsNone(dated["tasks"][0]["dueAt"])

        _, timed = self.run_cli(["due", task_id, "2026-08-07T09:30:00+02:00"])
        self.assertIsNone(timed["tasks"][0]["dueDate"])
        self.assertEqual(timed["tasks"][0]["dueAt"], "2026-08-07T09:30:00+02:00")

        _, updated_date = self.run_cli(["update", task_id, "--due", "2026-08-08"])
        self.assertEqual(updated_date["tasks"][0]["dueDate"], "2026-08-08")
        self.assertIsNone(updated_date["tasks"][0]["dueAt"])

        _, updated_time = self.run_cli(
            ["update", task_id, "--due", "2026-08-08T14:00:00Z"]
        )
        self.assertIsNone(updated_time["tasks"][0]["dueDate"])
        self.assertEqual(updated_time["tasks"][0]["dueAt"], "2026-08-08T14:00:00Z")

        _, cleared = self.run_cli(["update", task_id, "--due", "clear"])
        self.assertIsNone(cleared["tasks"][0]["dueDate"])
        self.assertIsNone(cleared["tasks"][0]["dueAt"])


if __name__ == "__main__":
    unittest.main()
