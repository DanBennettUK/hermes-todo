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
        self.assertIn("Start now", source)
        self.assertIn("expectedRevision: snapshot.revision", source)
        self.assertIn("?envelope=result", source)
        self.assertIn("const aPriority = a.priority || 99", source)
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


    def _run_drag_probe(self, body: str) -> str:
        """Execute the real drag lifecycle from plugin.js in Node with DOM stubs."""
        source = PLUGIN_SOURCE.read_text(encoding="utf-8")
        start = source.index("const dragPointerState = {")
        end = source.index("function resolveDropTarget(")
        # endPointerDrag + beginPointerDrag + the row-level press handler.
        press_start = source.index("  const handlePointerDown = event => {")
        press_end = source.index("  return jsxs('div', {", press_start)
        harness = f"""
{source[start:end]}
{source[press_start:press_end]}
const listeners = {{}}
const removed = []
// The row component supplies these; the extracted handler closes over them.
var draggable = true
var task = null
globalThis.window = {{
  addEventListener: (type, fn) => {{ (listeners[type] ||= []).push(fn) }},
  removeEventListener: (type, fn) => {{
    removed.push(type)
    const bucket = listeners[type] || []
    const at = bucket.indexOf(fn)
    if (at >= 0) bucket.splice(at, 1)
  }}
}}
globalThis.document = {{
  body: {{
    style: {{}},
    appendChild: node => {{ globalThis.__ghost = node; node.parentNode = globalThis.document.body }},
    removeChild: child => {{ globalThis.__ghostRemoved = true; child.parentNode = null }}
  }},
  createElement: () => ({{ style: {{}}, textContent: '', parentNode: null }}),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {{}}
}}
globalThis.setDragActive = value => {{ globalThis.__dragActive = value }}
globalThis.clearDropIndicator = () => {{ globalThis.__indicatorCleared = (globalThis.__indicatorCleared || 0) + 1 }}
globalThis.updateDropIndicator = () => {{}}
globalThis.dropTaskAt = (task, x, y) => {{ globalThis.__dropped = [task.id, x, y] }}
globalThis.setTimeout = () => 0
globalThis.clearTimeout = () => {{}}

function fire(type, event) {{
  for (const fn of [...(listeners[type] || [])]) fn(event)
}}
function liveCount() {{
  return Object.values(listeners).reduce((total, bucket) => total + bucket.length, 0)
}}
{body}
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", harness],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def test_drag_cancellation_above_threshold_clears_everything(self) -> None:
        out = self._run_drag_probe(
            """
task = { id: 't1', title: 'Move me' }
const row = { getBoundingClientRect: () => ({ left: 0, top: 0 }) }
handlePointerDown({
  button: 0, pointerId: 7, clientX: 10, clientY: 10,
  target: { closest: () => null }, currentTarget: row
})
fire('pointermove', { pointerId: 7, clientX: 60, clientY: 60 })
const armedState = {
  active: dragPointerState.active,
  dragActive: globalThis.__dragActive,
  userSelect: document.body.style.userSelect
}
fire('keydown', { key: 'Escape' })
console.log(JSON.stringify({
  armed: armedState,
  after: {
    active: dragPointerState.active,
    task: dragPointerState.task,
    pointerId: dragPointerState.pointerId,
    ghost: dragPointerState.ghost,
    cleanup: dragPointerState.cleanup,
    dragActive: globalThis.__dragActive,
    userSelect: document.body.style.userSelect,
    listenersLeft: liveCount(),
    dropped: globalThis.__dropped || null
  }
}))
"""
        )
        state = json.loads(out.strip())["armed"]
        self.assertTrue(state["active"], "threshold move should arm the drag")
        self.assertEqual(state["userSelect"], "none")

        after = json.loads(out.strip())["after"]
        self.assertFalse(after["active"], "Escape must disarm an active drag")
        self.assertIsNone(after["task"])
        self.assertIsNone(after["pointerId"])
        self.assertIsNone(after["ghost"])
        self.assertIsNone(after["cleanup"])
        self.assertFalse(after["dragActive"])
        self.assertEqual(after["userSelect"], "", "userSelect must be restored")
        self.assertEqual(after["listenersLeft"], 0, "every listener must be removed")
        self.assertIsNone(after["dropped"], "a cancelled drag must not commit")

    def test_drag_cancellation_below_threshold_disarms_without_ghosting(self) -> None:
        out = self._run_drag_probe(
            """
task = { id: 't2', title: 'Armed only' }
const row = { getBoundingClientRect: () => ({ left: 0, top: 0 }) }
handlePointerDown({
  button: 0, pointerId: 3, clientX: 10, clientY: 10,
  target: { closest: () => null }, currentTarget: row
})
fire('pointermove', { pointerId: 3, clientX: 12, clientY: 12 })
fire('keydown', { key: 'Escape' })
console.log(JSON.stringify({
  active: dragPointerState.active,
  task: dragPointerState.task,
  pointerId: dragPointerState.pointerId,
  cleanup: dragPointerState.cleanup,
  userSelect: document.body.style.userSelect,
  listenersLeft: liveCount()
}))
"""
        )
        after = json.loads(out.strip())
        self.assertFalse(after["active"])
        self.assertIsNone(after["task"], "Escape below threshold must clear the pending task")
        self.assertIsNone(after["pointerId"])
        self.assertIsNone(after["cleanup"])
        self.assertEqual(after["userSelect"], "")
        self.assertEqual(after["listenersLeft"], 0, "armed-phase listeners must be removed")

    def test_pointercancel_and_blur_both_disarm_the_drag(self) -> None:
        for trigger, event in (
            ("pointercancel", {"pointerId": 9}),
            ("blur", {}),
        ):
            with self.subTest(trigger=trigger):
                out = self._run_drag_probe(
                    f"""
task = {{ id: 't3', title: 'Cancel me' }}
const row = {{ getBoundingClientRect: () => ({{ left: 0, top: 0 }}) }}
handlePointerDown({{
  button: 0, pointerId: 9, clientX: 10, clientY: 10,
  target: {{ closest: () => null }}, currentTarget: row
}})
fire('pointermove', {{ pointerId: 9, clientX: 80, clientY: 80 }})
fire('{trigger}', {json.dumps(event)})
console.log(JSON.stringify({{
  active: dragPointerState.active,
  task: dragPointerState.task,
  listenersLeft: liveCount(),
  dropped: globalThis.__dropped || null
}}))
"""
                )
                after = json.loads(out.strip())
                self.assertFalse(after["active"], f"{trigger} must disarm")
                self.assertIsNone(after["task"])
                self.assertEqual(after["listenersLeft"], 0, f"{trigger} must detach listeners")
                self.assertIsNone(after["dropped"], f"{trigger} must not commit a drop")

    def test_escape_key_event_without_pointer_id_still_cancels(self) -> None:
        out = self._run_drag_probe(
            """
task = { id: 't4', title: 'Keyboard escape' }
const row = { getBoundingClientRect: () => ({ left: 0, top: 0 }) }
handlePointerDown({
  button: 0, pointerId: undefined, clientX: 10, clientY: 10,
  target: { closest: () => null }, currentTarget: row
})
fire('pointermove', { pointerId: undefined, clientX: 90, clientY: 90 })
const armed = dragPointerState.active
// A KeyboardEvent has no pointerId; it must not be filtered out by the guard.
fire('keydown', { key: 'Escape' })
console.log(JSON.stringify({
  armed,
  active: dragPointerState.active,
  listenersLeft: liveCount()
}))
"""
        )
        after = json.loads(out.strip())
        self.assertTrue(after["armed"])
        self.assertFalse(after["active"], "an Escape key event has no pointerId and must cancel")
        self.assertEqual(after["listenersLeft"], 0)

    def test_drop_indicator_resolves_a_line_on_the_first_move(self) -> None:
        source = PLUGIN_SOURCE.read_text(encoding="utf-8")
        body = source[source.index("function updateDropIndicator("):source.index("function clearDropIndicator(")]
        self.assertNotIn(
            "if (!dropContext.indicator) return",
            body,
            "the first drag must be able to show an indicator",
        )



if __name__ == "__main__":
    unittest.main()
