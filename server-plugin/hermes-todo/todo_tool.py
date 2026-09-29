"""Agent tool for the shared Hermes Todo board.

Exposes create/update/reorder/list/get (no delete) so the AI can run the
board without being able to destroy tasks. All actions call the store
directly and return compact JSON.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from .hermes_todo_store import (
        BoardError,
        RevisionConflict,
        create_subtask,
        create_task,
        delete_subtask,
        get_board,
        get_history,
        get_task,
        reorder_subtask,
        reorder_task,
        search_tasks,
        sort_subtasks,
        update_subtask,
        update_task,
    )
except ImportError:  # Direct source-tree execution and tests.
    from hermes_todo_store import (
        BoardError,
        RevisionConflict,
        create_subtask,
        create_task,
        delete_subtask,
        get_board,
        get_history,
        get_task,
        reorder_subtask,
        reorder_task,
        search_tasks,
        sort_subtasks,
        update_subtask,
        update_task,
    )

_CATEGORIES = ["today", "tomorrow", "this-week", "this-month", "soon"]
_PLANS = ["now", "today", "later"]
_STATUSES = ["open", "waiting", "blocked", "done"]

SCHEMA = {
    "name": "todo_board",
    "description": (
        "Read and write the shared Hermes Todo board. Actions: list "
        "(filter by category/plan/status/query), get (one task, optional "
        "history), create (new task with any settings), update (change any "
        "settings of an existing task: title, plan, status, category, "
        "position, due, priority, project, owner, brief, next_action, "
        "closure_condition, waiting_on, blocker, review_date, estimate), "
        "reorder (move a task within/between categories before or after a "
        "neighbour), start (make a task the single Now item), done (complete "
        "with optional closure note/evidence), subtask_create, subtask_update, "
        "subtask_delete, subtask_reorder, and subtask_sort. Parent-task delete "
        "is intentionally not supported."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list", "get", "create", "update", "reorder", "start", "done",
                    "subtask_create", "subtask_update", "subtask_delete", "subtask_reorder", "subtask_sort",
                ],
                "description": "Board operation to perform.",
            },
            "task_id": {"type": "string", "description": "Task id for get/update/reorder/start/done and all subtask actions."},
            "subtask_id": {"type": "string", "description": "Subtask id for subtask_update/subtask_delete/subtask_reorder."},
            "subtask_title": {"type": "string", "description": "subtask_create/subtask_update: checklist title, 1-500 characters."},
            "done": {"type": "boolean", "description": "subtask_update: true marks complete, false reopens."},
            "query": {"type": "string", "description": "list: text search across title and context fields."},
            "category": {"type": "string", "enum": _CATEGORIES, "description": "Category filter or target (today/tomorrow/this-week/this-month/soon)."},
            "plan": {"type": "string", "enum": _PLANS, "description": "Plan filter or value (now/today/later)."},
            "status": {"type": "string", "enum": _STATUSES, "description": "Status filter or value (open/waiting/blocked/done)."},
            "title": {"type": "string", "description": "create/update: task title."},
            "estimate": {"type": "integer", "description": "create/update: minutes, 5-480."},
            "priority": {"type": "integer", "description": "create/update: 1-4 (P1 highest), omit or null for normal."},
            "position": {"type": "number", "description": "create/update: explicit sort position."},
            "before_id": {"type": "string", "description": "reorder / subtask_reorder: place the item before this id."},
            "after_id": {"type": "string", "description": "reorder / subtask_reorder: place the item after this id."},
            "due_date": {"type": "string", "description": "create/update: all-day due date YYYY-MM-DD."},
            "due_at": {"type": "string", "description": "create/update: timed due ISO datetime."},
            "due_timezone": {"type": "string", "description": "create/update: IANA timezone for timed due."},
            "project": {"type": "string", "description": "create/update: project name."},
            "owner": {"type": "string", "description": "create/update: owner or assignee."},
            "brief": {"type": "string", "description": "create/update: durable context and decisions."},
            "next_action": {"type": "string", "description": "create/update: the smallest live move."},
            "closure_condition": {"type": "string", "description": "create/update: what proves completion."},
            "waiting_on": {"type": "string", "description": "update: waiting context (cleared with empty string)."},
            "review_date": {"type": "string", "description": "update: review date YYYY-MM-DD."},
            "blocker": {"type": "string", "description": "update: blocking reason (cleared with empty string)."},
            "closure_note": {"type": "string", "description": "done: what was delivered."},
            "evidence": {"type": "array", "items": {"type": "string"}, "description": "done: verification paths or links."},
            "expected_revision": {"type": "integer", "description": "Optimistic concurrency guard from a previous read."},
            "history_limit": {"type": "integer", "description": "get: include up to N history events."},
        },
        "required": ["action"],
    },
}


def _compact_task(task: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "title", "plan", "status", "category", "position", "estimate",
        "priority", "dueDate", "dueAt", "project", "owner", "inbox",
        "brief", "nextAction", "closureCondition", "waitingOn", "blocker",
        "reviewDate", "sessionId", "sessionState", "subtaskCount", "subtaskDoneCount",
    )
    compact = {key: task.get(key) for key in keys if task.get(key) is not None}
    if task.get("subtasks"):
        compact["subtasks"] = [
            {"id": item["id"], "title": item["title"], "done": item["done"]}
            for item in task["subtasks"]
        ]
    return compact


def _revision_kwargs(args: dict[str, Any]) -> dict[str, Any]:
    if args.get("expected_revision") is None:
        return {}
    return {"expected_revision": int(args["expected_revision"])}


def _apply(args: dict[str, Any]) -> dict[str, Any]:
    action = args.get("action")
    if action == "list":
        result = search_tasks(
            args.get("query"),
            plan=args.get("plan"),
            status=args.get("status"),
            category=args.get("category"),
            limit=min(int(args.get("limit", 50)), 200) if args.get("limit") else 50,
        )
        result["tasks"] = [_compact_task(task) for task in result["tasks"]]
        return result
    if action == "get":
        if not args.get("task_id"):
            raise BoardError("get requires task_id")
        result = get_task(args["task_id"])
        result["task"] = _compact_task(result["task"])
        if args.get("history_limit"):
            result["history"] = get_history(args["task_id"], limit=int(args["history_limit"]))["events"]
        return result
    fields = (
        ("title", "title"), ("estimate", "estimate"), ("priority", "priority"),
        ("due_date", "dueDate"), ("due_at", "dueAt"), ("due_timezone", "dueTimezone"),
        ("project", "project"), ("owner", "owner"), ("brief", "brief"),
        ("next_action", "nextAction"), ("closure_condition", "closureCondition"),
        ("waiting_on", "waitingOn"), ("review_date", "reviewDate"),
        ("blocker", "blocker"),
    )
    if action == "create":
        if not args.get("title"):
            raise BoardError("create requires title")
        kwargs: dict[str, Any] = {
            "event_source": "tool",
            "return_board": False,
            **_revision_kwargs(args),
        }
        for source, target in fields:
            if source != "title" and args.get(source) is not None:
                kwargs[target] = args[source]
        for source in ("category", "plan", "status", "position"):
            if args.get(source) is not None:
                kwargs[source] = args[source]
        result = create_task(args["title"], **kwargs)
        result["task"] = _compact_task(result["task"])
        return result
    if not args.get("task_id"):
        raise BoardError(f"{action} requires task_id")
    if action == "update":
        changes: dict[str, Any] = {}
        for source, target in fields:
            if source in args and args[source] is not None:
                changes[target] = args[source]
        for source in ("category", "plan", "status", "position"):
            if args.get(source) is not None:
                changes[source] = args[source]
        if not changes:
            raise BoardError("update produced no changes")
        result = update_task(
            args["task_id"],
            changes,
            event_source="tool",
            return_board=False,
            **_revision_kwargs(args),
        )
        result["task"] = _compact_task(result["task"])
        return result
    if action == "reorder":
        if not (args.get("category") or args.get("before_id") or args.get("after_id")):
            raise BoardError("reorder requires category, before_id, or after_id")
        result = reorder_task(
            args["task_id"],
            category=args.get("category"),
            before_id=args.get("before_id"),
            after_id=args.get("after_id"),
            event_source="tool",
            return_board=False,
            **_revision_kwargs(args),
        )
        result["task"] = _compact_task(result["task"])
        return result
    if action == "start":
        changes = {"status": "open", "waitingOn": None, "reviewDate": None, "blocker": None, "inbox": False}
        if args.get("category"):
            changes["category"] = args["category"]
        result = update_task(
            args["task_id"],
            {**changes, "plan": "now"},
            event_source="tool",
            return_board=False,
            **_revision_kwargs(args),
        )
        result["task"] = _compact_task(result["task"])
        return result
    if action == "done":
        changes: dict[str, Any] = {"status": "done"}
        if args.get("closure_note") is not None:
            changes["closureNote"] = args["closure_note"]
        if args.get("evidence") is not None:
            changes["closureEvidence"] = args["evidence"]
        result = update_task(
            args["task_id"],
            changes,
            event_source="tool",
            return_board=False,
            **_revision_kwargs(args),
        )
        result["task"] = _compact_task(result["task"])
        return result
    if action == "subtask_create":
        if not args.get("subtask_title"):
            raise BoardError("subtask_create requires subtask_title")
        result = create_subtask(
            args["task_id"],
            args["subtask_title"],
            done=bool(args.get("done", False)),
            expected_revision=int(args["expected_revision"]) if args.get("expected_revision") is not None else None,
            event_source="tool",
            return_board=False,
        )
        result["task"] = _compact_task(result["task"])
        if result.get("subtask"):
            result["subtask"] = {
                "id": result["subtask"]["id"],
                "title": result["subtask"]["title"],
                "done": result["subtask"]["done"],
            }
        return result
    if action == "subtask_update":
        if not args.get("subtask_id"):
            raise BoardError("subtask_update requires subtask_id")
        if args.get("subtask_title") is None and args.get("done") is None:
            raise BoardError("subtask_update requires subtask_title or done")
        result = update_subtask(
            args["task_id"],
            args["subtask_id"],
            title=args.get("subtask_title"),
            done=args.get("done"),
            expected_revision=int(args["expected_revision"]) if args.get("expected_revision") is not None else None,
            event_source="tool",
            return_board=False,
        )
        result["task"] = _compact_task(result["task"])
        return result
    if action == "subtask_delete":
        if not args.get("subtask_id"):
            raise BoardError("subtask_delete requires subtask_id")
        result = delete_subtask(
            args["task_id"],
            args["subtask_id"],
            expected_revision=int(args["expected_revision"]) if args.get("expected_revision") is not None else None,
            event_source="tool",
            return_board=False,
        )
        result["task"] = _compact_task(result["task"])
        return result
    if action == "subtask_reorder":
        if not args.get("subtask_id"):
            raise BoardError("subtask_reorder requires subtask_id")
        result = reorder_subtask(
            args["task_id"],
            args["subtask_id"],
            before_id=args.get("before_id"),
            after_id=args.get("after_id"),
            expected_revision=int(args["expected_revision"]) if args.get("expected_revision") is not None else None,
            event_source="tool",
            return_board=False,
        )
        result["task"] = _compact_task(result["task"])
        return result
    if action == "subtask_sort":
        result = sort_subtasks(
            args["task_id"],
            expected_revision=int(args["expected_revision"]) if args.get("expected_revision") is not None else None,
            event_source="tool",
            return_board=False,
        )
        result["task"] = _compact_task(result["task"])
        return result
    raise BoardError(f"Unknown action: {action}")


def todo_board(args: dict, **kwargs) -> str:
    """Dispatch the todo_board tool. Returns JSON text."""
    try:
        return json.dumps({"ok": True, **_apply(args)}, ensure_ascii=False, default=str)
    except RevisionConflict as exc:
        return json.dumps({
            "ok": False,
            "error": "revision_conflict",
            "message": str(exc),
            "expectedRevision": exc.expected_revision,
            "currentRevision": exc.current_revision,
        })
    except KeyError:
        return json.dumps({"ok": False, "error": "not_found", "message": "Task not found"})
    except BoardError as exc:
        return json.dumps({"ok": False, "error": "invalid_request", "message": str(exc)})
    except (TypeError, ValueError) as exc:
        return json.dumps({"ok": False, "error": "invalid_request", "message": str(exc)})
