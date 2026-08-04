"""Operator CLI for the shared Hermes Todo board."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from pathlib import Path

try:
    from .hermes_todo_store import BoardError, create_task, delete_task, get_board, import_tasks, update_task
except ImportError:  # Direct source-tree execution and tests.
    from hermes_todo_store import BoardError, create_task, delete_task, get_board, import_tasks, update_task


def register_cli(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="todo_action")

    list_parser = actions.add_parser("list", aliases=["ls"], help="Show the shared board")
    list_parser.add_argument("--plan", choices=["now", "today", "later"])
    list_parser.add_argument("--status", choices=["open", "waiting", "blocked", "done"])

    add_parser = actions.add_parser("add", help="Add a task")
    add_parser.add_argument("title")
    add_parser.add_argument("--estimate", type=int, default=25)
    add_parser.add_argument("--plan", choices=["now", "today", "later"], default="today")
    add_parser.add_argument("--status", choices=["open", "waiting", "blocked", "done"], default="open")
    add_parser.add_argument("--due")
    add_parser.add_argument("--project")
    add_parser.add_argument("--priority", type=int, choices=[1, 2, 3, 4])
    add_parser.add_argument("--source")
    add_parser.add_argument("--external-id")
    add_parser.add_argument("--recurrence")

    for name, changes, help_text in (
        ("focus", {"plan": "now", "status": "open"}, "Make a task the single Now item"),
        ("today", {"plan": "today", "status": "open"}, "Plan an open task for Today"),
        ("later", {"plan": "later", "status": "open"}, "Move an open task to Later"),
        ("wait", {"status": "waiting"}, "Mark a task Waiting"),
        ("block", {"status": "blocked"}, "Mark a task Blocked"),
        ("done", {"status": "done"}, "Complete a task"),
        ("reopen", {"status": "open"}, "Reopen a task in its previous plan"),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("task_id")
        action.set_defaults(todo_changes=changes)

    due_parser = actions.add_parser("due", help="Set or clear a due date")
    due_parser.add_argument("task_id")
    due_parser.add_argument("date", help="ISO date/datetime, or 'clear'")

    update_parser = actions.add_parser("update", help="Change task details")
    update_parser.add_argument("task_id")
    update_parser.add_argument("--title")
    update_parser.add_argument("--estimate", type=int)
    update_parser.add_argument("--plan", choices=["now", "today", "later"])
    update_parser.add_argument("--status", choices=["open", "waiting", "blocked", "done"])
    update_parser.add_argument("--due")
    update_parser.add_argument("--project")
    update_parser.add_argument("--priority", type=int, choices=[1, 2, 3, 4])

    import_parser = actions.add_parser("import", help="One-time import from a JSON board or task array")
    import_parser.add_argument("path", help="Path to a JSON file")

    delete_parser = actions.add_parser("delete", help="Delete a task")
    delete_parser.add_argument("task_id")

    parser.set_defaults(func=todo_command)


def _compact(
    board: dict[str, Any],
    plan: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    tasks = board.get("tasks") or []
    if plan:
        tasks = [task for task in tasks if task.get("plan") == plan]
    if status:
        tasks = [task for task in tasks if task.get("status") == status]
    return {
        "version": board.get("version"),
        "revision": board.get("revision"),
        "tasks": tasks,
    }


def _due_changes(value: str) -> dict[str, str | None]:
    """Map CLI due input onto the store's independent date/time fields."""
    if value == "clear":
        return {"dueDate": None, "dueAt": None}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return {"dueDate": value, "dueAt": None}
    return {"dueDate": None, "dueAt": value}


def todo_command(args: argparse.Namespace) -> int:
    action = getattr(args, "todo_action", None)
    if not action:
        print("Usage: hermes todo {list|add|focus|today|later|wait|block|done|reopen|due|update|import|delete}")
        return 2

    try:
        if action in {"list", "ls"}:
            board = get_board()
            output = _compact(
                board,
                getattr(args, "plan", None),
                getattr(args, "status", None),
            )
        elif action == "add":
            due_changes = _due_changes(args.due) if args.due is not None else {}
            output = create_task(
                args.title,
                estimate=args.estimate,
                plan=args.plan,
                status=args.status,
                due_date=due_changes.get("dueDate"),
                due_at=due_changes.get("dueAt"),
                project=args.project,
                priority=args.priority,
                source=args.source,
                external_id=args.external_id,
                recurrence=args.recurrence,
            )
        elif action in {"focus", "today", "later", "wait", "block", "done", "reopen"}:
            output = update_task(args.task_id, args.todo_changes)
        elif action == "due":
            output = update_task(args.task_id, _due_changes(args.date))
        elif action == "update":
            changes = {
                key: value
                for key, value in {
                    "title": args.title,
                    "estimate": args.estimate,
                    "plan": args.plan,
                    "status": args.status,
                    "project": args.project,
                    "priority": args.priority,
                }.items()
                if value is not None
            }
            if args.due is not None:
                changes.update(_due_changes(args.due))
            output = update_task(args.task_id, changes)
        elif action == "import":
            payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
            tasks = payload.get("tasks", []) if isinstance(payload, dict) else payload
            if not isinstance(tasks, list):
                raise BoardError("Import JSON must be a task array or an object with a tasks array")
            output = import_tasks(tasks)
        elif action == "delete":
            output = delete_task(args.task_id)
        else:
            print(f"Unknown todo action: {action}")
            return 2
    except KeyError:
        print(json.dumps({"ok": False, "error": "Task not found"}))
        return 1
    except (BoardError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    print(json.dumps({"ok": True, **_compact(output)}, indent=2))
    return 0
