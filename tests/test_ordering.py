"""Reorder anchor validation shared with the store's positional-ordering contract."""

import gc
import os
import tempfile
import unittest

import hermes_todo_store as store


class OrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.previous_home = os.environ.get("HERMES_HOME")
        self.previous_resolver = store._get_hermes_home
        os.environ["HERMES_HOME"] = self.home.name
        store._get_hermes_home = None
        self.tasks = [store.create_task(title, return_board=False)["task"] for title in "ABCD"]

    def tearDown(self) -> None:
        store._get_hermes_home = self.previous_resolver
        if self.previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self.previous_home
        gc.collect()
        self.home.cleanup()

    def order(self):
        tasks = sorted(store.get_board()["tasks"], key=lambda task: task["position"])
        self.assertEqual(len({task["position"] for task in tasks}), len(tasks))
        return [task["title"] for task in tasks]

    def test_invalid_anchors_are_atomic_even_at_current_revision(self) -> None:
        a, b, c, d = [task["id"] for task in self.tasks]
        for before, after in [(c, a), (a, b), (b, b), (d, c), (c, d), (d, None), (None, d)]:
            with self.subTest(before=before, after=after):
                board = store.get_board()
                history = store.get_history(d)
                with self.assertRaises(store.BoardError):
                    store.reorder_task(
                        d,
                        before_id=before,
                        after_id=after,
                        expected_revision=board["revision"],
                    )
                self.assertEqual(store.get_board(), board)
                self.assertEqual(store.get_history(d), history)

    def test_adjacent_anchors_exclude_the_moving_task(self) -> None:
        a, b, c, d = [task["id"] for task in self.tasks]
        store.reorder_task(b, after_id=a, before_id=c)
        self.assertEqual(self.order(), list("ABCD"))
        store.reorder_task(d, after_id=a, before_id=b)
        self.assertEqual(self.order(), list("ADBC"))

    def test_single_anchor_moves_do_not_use_the_moving_task_as_a_bound(self) -> None:
        _, b, c, _ = [task["id"] for task in self.tasks]
        result = store.reorder_task(b, before_id=c, return_board=False)
        self.assertEqual(result["task"]["position"], self.tasks[1]["position"])
        result = store.reorder_task(c, after_id=b, return_board=False)
        self.assertEqual(result["task"]["position"], self.tasks[2]["position"])

    def test_stale_revision_and_cross_category_are_atomic(self) -> None:
        a, b, _, d = [task["id"] for task in self.tasks]
        board = store.get_board()
        with self.assertRaises(store.RevisionConflict):
            store.reorder_task(d, after_id=a, before_id=b, expected_revision=board["revision"] - 1)
        with self.assertRaises(store.BoardError):
            store.reorder_task(d, category="soon", before_id=b)
        self.assertEqual(store.get_board(), board)
        store.reorder_task(d, category="soon")
        store.reorder_task(d, category="today", after_id=a, before_id=b)
        self.assertEqual(self.order(), list("ADBC"))

    def test_equal_adjacent_positions_rebalance(self) -> None:
        # Two tasks at the same position tie-break on id, so the (after, before)
        # pair has to be read from the real order instead of assumed as (A, B).
        by_title = {task["title"]: task["id"] for task in self.tasks}
        store.update_task(by_title["A"], {"position": 1})
        store.update_task(by_title["B"], {"position": 1})
        ordered = sorted(
            store.get_board()["tasks"],
            key=lambda task: (task["position"], task["createdAt"], task["id"]),
        )
        tied = [task["title"] for task in ordered if task["position"] == 1]
        self.assertEqual(sorted(tied), ["A", "B"])
        store.reorder_task(
            by_title["D"],
            after_id=by_title[tied[0]],
            before_id=by_title[tied[1]],
        )
        titles = self.order()
        self.assertEqual(sorted(titles), list("ABCD"))
        # The moved task must land between the two tied anchors.
        self.assertLess(titles.index(tied[0]), titles.index("D"))
        self.assertLess(titles.index("D"), titles.index(tied[1]))


if __name__ == "__main__":
    unittest.main()
