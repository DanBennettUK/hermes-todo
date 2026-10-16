from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import hermes_todo_store as store
from cli import register_cli
from hermes_todo_store import BoardError, create_task, get_board
from todo_tool import todo_board


class HermesTodoProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.previous_home = os.environ.get("HERMES_HOME")
        self.previous_profile = os.environ.get("HERMES_TODO_PROFILE")
        self.previous_resolver = store._get_hermes_home
        store._get_hermes_home = None
        os.environ["HERMES_HOME"] = str(self.root)
        os.environ.pop("HERMES_TODO_PROFILE", None)

    def tearDown(self) -> None:
        store._get_hermes_home = self.previous_resolver
        self._restore("HERMES_HOME", self.previous_home)
        self._restore("HERMES_TODO_PROFILE", self.previous_profile)
        self.tmp.cleanup()

    @staticmethod
    def _restore(name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def _use(self, name: str) -> None:
        os.environ["HERMES_TODO_PROFILE"] = name

    def test_unset_profile_keeps_the_active_home(self) -> None:
        os.environ["HERMES_TODO_PROFILE"] = "   "
        create_task("Stay here")
        self.assertEqual(get_board()["tasks"][0]["title"], "Stay here")
        self.assertEqual(store.resolve_hermes_home(), self.root)
        self.assertNotIn("todoProfile", get_board())
        self.assertTrue((self.root / "hermes-todo" / "todo.sqlite3").is_file())

    def test_named_profile_is_the_store_the_cli_and_the_tool_read(self) -> None:
        work = self.root / "profiles" / "work"
        work.mkdir(parents=True)
        self._use("Work")
        create_task("From the selected profile")
        self.assertEqual(store.resolve_hermes_home(), work)
        board = get_board()
        self.assertEqual(board["todoProfile"], "work")
        self.assertEqual(board["tasks"][0]["title"], "From the selected profile")
        self.assertFalse((self.root / "hermes-todo" / "todo.sqlite3").exists())
        self.assertTrue((work / "hermes-todo" / "todo.sqlite3").is_file())

        parser = argparse.ArgumentParser()
        register_cli(parser)
        args = parser.parse_args(["list"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = args.func(args)
        listed = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual([task["title"] for task in listed["tasks"]], ["From the selected profile"])

        tool_board = json.loads(todo_board({"action": "list"}))
        self.assertEqual(
            [task["title"] for task in tool_board["tasks"]],
            ["From the selected profile"],
        )

    def test_default_from_a_named_home_uses_the_hermes_root(self) -> None:
        field = self.root / "profiles" / "field"
        field.mkdir(parents=True)
        store._get_hermes_home = lambda: field
        self._use("default")
        create_task("On the default profile")
        self.assertEqual(store.resolve_hermes_home(), self.root)
        self.assertEqual(get_board()["todoProfile"], "default")
        self.assertTrue((self.root / "hermes-todo" / "todo.sqlite3").is_file())
        self.assertFalse((field / "hermes-todo" / "todo.sqlite3").exists())

    def test_missing_or_invalid_profile_does_not_create_a_directory(self) -> None:
        self._use("missing")
        with self.assertRaises(BoardError) as missing:
            create_task("Should not land")
        self.assertIn("missing", str(missing.exception))
        self.assertFalse((self.root / "profiles" / "missing").exists())

        self._use("../secret")
        with self.assertRaises(BoardError) as invalid:
            create_task("Should not land")
        self.assertIn("not a Hermes profile name", str(invalid.exception))
        self.assertFalse((self.root / "secret").exists())
        self.assertFalse((self.root / "hermes-todo").exists())
