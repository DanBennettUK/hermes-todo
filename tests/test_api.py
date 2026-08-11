from __future__ import annotations

import os
import tempfile
import unittest

import hermes_todo_store as store
from hermes_todo_store import create_task

try:
    from fastapi import HTTPException
    from pydantic import ValidationError

    from dashboard.plugin_api import (
        CompleteFollowUpBody,
        SessionLinkBody,
        TaskCreate,
        TaskPatch,
        add_task,
        complete_and_follow_up,
        link_session,
        patch_task,
        read_agenda,
        read_task_history,
        read_tasks,
    )
except ModuleNotFoundError as exc:
    if exc.name not in {"fastapi", "pydantic"}:
        raise
    API_DEPENDENCIES_AVAILABLE = False
else:
    API_DEPENDENCIES_AVAILABLE = True


@unittest.skipUnless(
    API_DEPENDENCIES_AVAILABLE,
    "FastAPI and Pydantic are supplied by the Hermes host environment",
)
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
        unchanged = patch_task(task_id, omitted, envelope="result")
        self.assertEqual(unchanged["task"]["dueDate"], "2026-08-03")

        cleared = TaskPatch(dueDate=None)
        self.assertEqual(cleared.model_dump(exclude_unset=True), {"due_date": None})
        result = patch_task(task_id, cleared, envelope="result")
        self.assertIsNone(result["task"]["dueDate"])

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
            envelope="result",
        )
        task = result["task"]
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

    def test_create_patch_search_agenda_and_history_contracts(self) -> None:
        created = add_task(
            TaskCreate(
                title="API slice",
                plan="later",
                inbox=True,
                brief="Durable context",
                nextAction="Exercise the API",
                closureCondition="Contract passes",
                artefacts=["tests/test_api.py"],
                owner="Dan",
                executionMode="supervised",
                approvalState="pending",
                expectedRevision=0,
                eventSource="desktop",
            ),
            envelope="result",
        )
        self.assertEqual(created["revision"], 1)
        self.assertEqual((created["task"]["inbox"], created["task"]["plan"]), (True, "later"))
        task_id = created["task"]["id"]

        patched = patch_task(
            task_id,
            TaskPatch(
                title="API slice ready",
                artefacts=["tests/test_api.py", "README.md"],
                expectedRevision=1,
            ),
            envelope="result",
        )
        self.assertEqual((patched["revision"], patched["task"]["title"]), (2, "API slice ready"))
        searched = read_tasks(q="durable", owner="dan", limit=100)
        self.assertEqual(searched["total"], 1)
        agenda = read_agenda(on_date="2026-08-09", timezone_name="UTC", stale_days=14, limit=50)
        self.assertEqual(agenda["items"][0]["reasons"][0]["code"], "inbox")
        history = read_task_history(task_id, limit=100)
        self.assertIn("task.retitled", {event["type"] for event in history["events"]})
        self.assertIn("artefact.attached", {event["type"] for event in history["events"]})

    def test_revision_conflict_returns_machine_readable_409(self) -> None:
        created = add_task(
            TaskCreate(title="Concurrent", expectedRevision=0), envelope="result"
        )
        task_id = created["task"]["id"]
        patch_task(
            task_id,
            TaskPatch(brief="writer one", expectedRevision=1),
            envelope="result",
        )
        with self.assertRaises(HTTPException) as caught:
            patch_task(
                task_id,
                TaskPatch(nextAction="writer two", expectedRevision=1),
                envelope="result",
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["error"], "revision_conflict")
        self.assertEqual(caught.exception.detail["currentRevision"], 2)

    def test_board_envelope_and_empty_read_contracts(self) -> None:
        empty_search = read_tasks(limit=100)
        self.assertEqual((empty_search["total"], empty_search["tasks"]), (0, []))
        empty_agenda = read_agenda(on_date="2026-08-09", timezone_name="UTC", stale_days=14, limit=50)
        self.assertEqual((empty_agenda["total"], empty_agenda["items"]), (0, []))
        default_board = add_task(TaskCreate(title="Legacy default consumer"))
        self.assertEqual(len(default_board["tasks"]), 1)
        explicit_board = add_task(TaskCreate(title="Explicit board consumer"), envelope="board")
        self.assertEqual(len(explicit_board["tasks"]), 2)
        compact = add_task(TaskCreate(title="Compact consumer"), envelope="result")
        self.assertEqual(compact["task"]["title"], "Compact consumer")

    def test_board_envelopes_keep_generated_and_follow_up_tasks(self) -> None:
        recurring = add_task(
            TaskCreate(
                title="Recurring API task",
                dueDate="2026-08-09",
                recurrenceRule="daily",
            ),
            envelope="result",
        )
        completed = patch_task(
            recurring["task"]["id"],
            TaskPatch(status="done", expectedRevision=recurring["revision"]),
        )
        self.assertEqual(len(completed["tasks"]), 2)
        self.assertIn("generatedTask", completed)
        self.assertIn(completed["generatedTask"]["id"], {task["id"] for task in completed["tasks"]})

        parent = add_task(
            TaskCreate(title="Follow-up API parent", closureNote="Keep me"),
            envelope="result",
        )
        body = CompleteFollowUpBody(
            followUp={"title": "Follow-up API child"},
            expectedRevision=parent["revision"],
        )
        self.assertNotIn("closure_note", body.model_dump(exclude_unset=True))
        followed = complete_and_follow_up(parent["task"]["id"], body)
        self.assertIn("followUpTask", followed)
        self.assertIn(followed["followUpTask"]["id"], {task["id"] for task in followed["tasks"]})
        completed_parent = next(
            task for task in followed["tasks"] if task["id"] == parent["task"]["id"]
        )
        self.assertEqual(completed_parent["closureNote"], "Keep me")

    def test_complete_follow_up_explicit_null_and_empty_clear_closure(self) -> None:
        parent = add_task(
            TaskCreate(
                title="Clear API closure",
                closureNote="Remove me",
                closureEvidence=["remove-me.txt"],
            ),
            envelope="result",
        )
        body = CompleteFollowUpBody(
            followUp={"title": "Clear API follow-up"},
            closureNote=None,
            closureEvidence=[],
            expectedRevision=parent["revision"],
        )
        dumped = body.model_dump(exclude_unset=True)
        self.assertIn("closure_note", dumped)
        self.assertIn("closure_evidence", dumped)
        result = complete_and_follow_up(
            parent["task"]["id"], body, envelope="result"
        )
        self.assertIsNone(result["task"]["closureNote"])
        self.assertEqual(result["task"]["closureEvidence"], [])

    def test_session_link_reuses_active_reference(self) -> None:
        created = add_task(
            TaskCreate(title="Session task", plan="later"), envelope="result"
        )
        task_id = created["task"]["id"]
        linked = link_session(
            task_id,
            SessionLinkBody(sessionId="stored-session", expectedRevision=1, eventSource="desktop"),
            envelope="result",
        )
        self.assertEqual((linked["task"]["sessionId"], linked["task"]["plan"]), ("stored-session", "now"))
        repeated = link_session(
            task_id,
            SessionLinkBody(sessionId="stored-session", expectedRevision=2, eventSource="desktop"),
            envelope="result",
        )
        self.assertEqual(repeated["revision"], 2)


if __name__ == "__main__":
    unittest.main()
