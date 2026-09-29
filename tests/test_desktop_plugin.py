from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


PLUGIN_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "desktop-plugin"
    / "hermes-todo"
    / "plugin.js"
)


class HermesTodoDesktopContractTests(unittest.TestCase):
    def test_remote_board_uses_profile_scoped_plugin_rest(self) -> None:
        source = PLUGIN_SOURCE.read_text(encoding="utf-8")

        self.assertIn("return ctx.rest(path, options)", source)
        self.assertEqual(source.count("ctx.rest("), 1)
        self.assertNotIn("hermesDesktop?.api", source)
        self.assertNotIn("/api/plugins/", source)
        self.assertNotIn("globalThis.window", source)

    def test_capture_board_priority_and_revision_contracts_are_wired(self) -> None:
        source = PLUGIN_SOURCE.read_text(encoding="utf-8")

        self.assertIn("plan: 'later'", source)
        self.assertIn("inbox: true", source)
        self.assertIn("Capture to Inbox", source)
        self.assertIn("Captured tasks wait here until you start or plan them.", source)
        self.assertIn("Drop tasks here to park them.", source)
        self.assertIn("Start now", source)
        self.assertIn("expectedRevision: snapshot.revision", source)
        self.assertIn("?envelope=result", source)
        self.assertIn("const aPriority = a.priority || 99", source)
        self.assertIn("label: 'P4'", source)
        self.assertIn("PRIORITY_PILL[task.priority]", source)
        self.assertIn("jsx(BoardView, { remote, rowProps, sections })", source)
        self.assertNotIn("loadAgenda", source)
        self.assertNotIn("agendaQuery", source)
        self.assertNotIn("Start Here", source)
        self.assertNotIn("const hasOpenNow", source)

    def test_task_details_only_send_fields_changed_from_the_opening_snapshot(self) -> None:
        source = PLUGIN_SOURCE.read_text(encoding="utf-8")

        self.assertIn("function changedTaskDetails(initial, current)", source)
        self.assertIn("function sameTaskDetailValue(left, right)", source)
        self.assertIn("const initialDraftRef = useRef(null)", source)
        self.assertIn("changedTaskDetails(initialDraftRef.current, currentDraft", source)
        self.assertIn("Object.keys(changes).length === 0", source)
        self.assertIn("await update(task.id, changes)", source)
        self.assertIn("SegmentedControl", source)
        self.assertIn("icons.Save", source)
        self.assertIn("const DETAIL_TABS", source)
        self.assertLess(source.index("...tabFields"), source.index("children: 'Cancel'"))
        self.assertIn("Confirm delete", source)
        self.assertIn("id: 'subs'", source)
        self.assertIn("function SubtaskEditor", source)
        self.assertIn("task.subtaskCount > 0", source)
        self.assertIn("data-todo-card-subtask", source)
        self.assertIn("done: !item.done", source)
        self.assertNotIn("paddingLeft: 4", source)
        self.assertIn("'due-row'", source)
        self.assertIn("'aria-label': 'Due date'", source)
        self.assertIn("function DimInput", source)
        self.assertIn("DIM_FIELD_BORDER", source)
        self.assertIn("initial.dueMode !== current.dueMode", source)
        self.assertIn("artefacts: lines(artefactsDraft)", source)
        self.assertIn("closureEvidence: lines(closureEvidenceDraft)", source)

    def test_work_prompt_carries_durable_reentry_context_without_private_payload(self) -> None:
        source = PLUGIN_SOURCE.read_text(encoding="utf-8")
        self.assertIn("function buildWorkPrompt(task)", source)
        self.assertNotIn("from './work-prompt.mjs'", source)
        self.assertIn(
            "text: buildWorkPrompt({ ...task, plan: 'now', inbox: false, sessionId: createdSession.stored_session_id, sessionState: 'active' })",
            source,
        )
        self.assertIn("title: task.title.slice(0, 160)", source)

        task = {
            "id": "task-123",
            "title": "Refresh dashboard " + ("x" * 500),
            "plan": "now",
            "status": "open",
            "estimate": 25,
            "brief": "Carry forward the approved dashboard scope.",
            "nextAction": "Run the focused regression suite.",
            "closureCondition": "The prompt contract test passes.",
            "waitingOn": "Reviewer",
            "artefacts": ["tests/test_desktop_plugin.py", "desktop-plugin/hermes-todo/plugin.js"],
            "source": "desktop",
            "externalId": "capture-123",
            "sourcePayload": {"private": "PRIVATE_SOURCE_PAYLOAD"},
            "closureNote": "HISTORICAL_CLOSURE_NOTE",
            "closureEvidence": ["HISTORICAL_CLOSURE_EVIDENCE"],
            "completedAt": "HISTORICAL_COMPLETED_AT",
        }
        node_script = """
        import { readFileSync } from 'node:fs'
        const source = readFileSync('./desktop-plugin/hermes-todo/plugin.js', 'utf8')
        const start = source.indexOf('function buildWorkPrompt(task) {')
        const end = source.indexOf('function makeId()', start)
        if (start < 0 || end < 0) throw new Error('inline buildWorkPrompt function not found')
        const buildWorkPrompt = new Function(`${source.slice(start, end)}; return buildWorkPrompt`)()
        const task = JSON.parse(readFileSync(0, 'utf8'))
        process.stdout.write(buildWorkPrompt(task))
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", node_script],
            cwd=PLUGIN_SOURCE.parents[2],
            input=json.dumps(task),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        prompt = result.stdout

        self.assertIn(f"Task: {task['title'][:160]}", prompt)
        self.assertNotIn(f"Task: {task['title'][:161]}", prompt)
        self.assertIn("Brief and prior decisions:\nCarry forward the approved dashboard scope.", prompt)
        self.assertIn("Next action: Run the focused regression suite.", prompt)
        self.assertIn("Closure condition: The prompt contract test passes.", prompt)
        self.assertIn("Waiting on: Reviewer", prompt)
        self.assertIn("Artefacts:\n- tests/test_desktop_plugin.py\n- desktop-plugin/hermes-todo/plugin.js", prompt)
        self.assertIn("Origin: desktop (capture-123)", prompt)
        for private_or_historical in (
            "PRIVATE_SOURCE_PAYLOAD",
            "HISTORICAL_CLOSURE_NOTE",
            "HISTORICAL_CLOSURE_EVIDENCE",
            "HISTORICAL_COMPLETED_AT",
        ):
            self.assertNotIn(private_or_historical, prompt)

    def test_history_and_linked_session_resume_are_wired(self) -> None:
        source = PLUGIN_SOURCE.read_text(encoding="utf-8")

        self.assertIn("/history?limit=40", source)
        self.assertIn("/session/complete", source)
        self.assertIn("task.sessionId && task.sessionState === 'active'", source)
        self.assertIn("Resume with Hermes", source)
        self.assertIn("Close linked session", source)
        self.assertIn("completeSession(task.id)", source)
        self.assertIn("remote.linkSession(task.id, createdSession.stored_session_id)", source)
        self.assertLess(
            source.index("if (task.sessionId && task.sessionState === 'active')"),
            source.index("createdSession = await host.request('session.create'"),
        )
        self.assertLess(
            source.index("if (!linked)"),
            source.index("if (linked) await remote.completeSession(task.id)"),
        )

    def test_due_helpers_use_task_timezone_with_a_safe_legacy_fallback(self) -> None:
        source = PLUGIN_SOURCE.read_text(encoding="utf-8")

        self.assertIn("function safeTimeZone(value)", source)
        self.assertIn("function zonedDateTimeToDate(value, timeZone)", source)
        self.assertIn("return localDateKey(due)", source)
        self.assertIn("const taskTimeZone = safeTimeZone(task.dueTimezone)", source)
        self.assertIn("dueAt: localDateTimeValue(task.dueAt, task.dueTimezone)", source)
        self.assertIn("timeZone: taskTimeZone", source)


if __name__ == "__main__":
    unittest.main()
