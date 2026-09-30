"""Subtask reorder precision: saturation, adjacency and atomicity."""

import math
import os
import sqlite3
import tempfile
import unittest

import hermes_todo_store as store
from hermes_todo_store import (
    BoardError,
    RevisionConflict,
    create_subtask,
    create_task,
    get_board,
    get_task,
    reorder_subtask,
    resolve_db_path,
)


class SubtaskOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_home = os.environ.get("HERMES_HOME")
        self.previous_resolver = store._get_hermes_home
        os.environ["HERMES_HOME"] = self.tmp.name
        store._get_hermes_home = None
        self.task = create_task("Parent", return_board=False)["task"]
        self.items = [
            create_subtask(self.task["id"], title, return_board=False)["subtask"]
            for title in ("A", "B", "C", "D")
        ]

    def tearDown(self) -> None:
        store._get_hermes_home = self.previous_resolver
        if self.previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self.previous_home
        self.tmp.cleanup()

    def positions(self):
        return [item["position"] for item in get_task(self.task["id"])["task"]["subtasks"]]

    def titles(self):
        return [item["title"] for item in get_task(self.task["id"])["task"]["subtasks"]]

    def force_positions(self, **values) -> None:
        conn = sqlite3.connect(resolve_db_path())
        try:
            for title, position in values.items():
                conn.execute(
                    "UPDATE subtasks SET position = ? WHERE id = ?",
                    (position, self.by_title[title]),
                )
            conn.commit()
        finally:
            conn.close()

    def setUpClass_attrs(self) -> None:
        self.by_title = {item["title"]: item["id"] for item in self.items}

    def test_adjacent_positions_one_ulp_apart_do_not_duplicate(self) -> None:
        # Two neighbours a single ULP apart round the midpoint back onto the
        # lower bound, which used to persist a duplicate position.
        by_title = {item["title"]: item["id"] for item in self.items}
        self.by_title = by_title
        self.force_positions(A=1.0, B=math.nextafter(1.0, 2.0))
        before = {item["title"]: item["position"] for item in get_task(self.task["id"])["task"]["subtasks"]}
        revision = get_board()["revision"]

        reorder_subtask(
            self.task["id"],
            by_title["C"],
            after_id=by_title["A"],
            before_id=by_title["B"],
            expected_revision=revision,
        )

        after = {item["title"]: item["position"] for item in get_task(self.task["id"])["task"]["subtasks"]}
        self.assertEqual(len(set(after.values())), len(after), "positions must stay unique")
        self.assertLess(after["A"], after["C"])
        self.assertLess(after["C"], after["B"])
        self.assertNotEqual(after["C"], before["A"])

    def test_repeated_saturation_never_creates_a_duplicate(self) -> None:
        by_title = {item["title"]: item["id"] for item in self.items}
        self.by_title = by_title
        for _ in range(4):
            current = {item["title"]: item["id"] for item in get_task(self.task["id"])["task"]["subtasks"]}
            ordered = [item["title"] for item in get_task(self.task["id"])["task"]["subtasks"]]
            if len(ordered) < 3:
                break
            # Narrow the two outer neighbours until the gap is exhausted.
            first, second = ordered[0], ordered[1]
            self.force_positions(**{first: 1.0, second: math.nextafter(1.0, 2.0)})
            mover = next(title for title in ordered[2:] if title in current)
            try:
                reorder_subtask(
                    self.task["id"],
                    current[mover],
                    after_id=current[first],
                    before_id=current[second],
                )
            except BoardError:
                pass
            positions = self.positions()
            self.assertEqual(len(set(positions)), len(positions), f"duplicate after {mover}")

    def test_non_adjacent_anchors_are_rejected(self) -> None:
        by_title = {item["title"]: item["id"] for item in self.items}
        revision = get_board()["revision"]
        board = get_board()
        with self.assertRaises(BoardError):
            reorder_subtask(
                self.task["id"],
                by_title["D"],
                after_id=by_title["A"],
                before_id=by_title["C"],
                expected_revision=revision,
            )
        self.assertEqual(get_board(), board, "a rejected reorder must not write")

    def test_subtask_cannot_be_its_own_neighbour(self) -> None:
        by_title = {item["title"]: item["id"] for item in self.items}
        with self.assertRaises(BoardError):
            reorder_subtask(
                self.task["id"],
                by_title["B"],
                after_id=by_title["B"],
                before_id=by_title["C"],
            )
        with self.assertRaises(BoardError):
            reorder_subtask(
                self.task["id"],
                by_title["B"],
                after_id=by_title["A"],
                before_id=by_title["B"],
            )

    def test_stale_revision_is_atomic(self) -> None:
        by_title = {item["title"]: item["id"] for item in self.items}
        board = get_board()
        with self.assertRaises(RevisionConflict):
            reorder_subtask(
                self.task["id"],
                by_title["D"],
                before_id=by_title["A"],
                expected_revision=board["revision"] - 1,
            )
        self.assertEqual(get_board(), board)


if __name__ == "__main__":
    unittest.main()
