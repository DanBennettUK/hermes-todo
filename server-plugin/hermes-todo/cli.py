"""Operator CLI for the shared Hermes Todo board."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    from .hermes_todo_store import (
        BoardError,
        RevisionConflict,
        complete_task_session,
        complete_with_follow_up,
        create_task,
        delete_task,
        generate_next_occurrence,
        get_agenda,
        get_history,
        get_task,
        import_tasks,
        link_task_session,
        reorder_task,
        search_tasks,
        update_task,
    )
except ImportError:  # Direct source-tree execution and tests.
    from hermes_todo_store import (
        BoardError,
        RevisionConflict,
        complete_task_session,
        complete_with_follow_up,
        create_task,
        delete_task,
        generate_next_occurrence,
        get_agenda,
        get_history,
        get_task,
        import_tasks,
        link_task_session,
        reorder_task,
        search_tasks,
        update_task,
    )


def _mutation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--actor")
    envelope = parser.add_mutually_exclusive_group()
    envelope.add_argument(
        "--compact",
        action="store_true",
        help="Return only the compact mutation result",
    )
    envelope.add_argument(
        "--board",
        action="store_true",
        help="Return the full-board envelope (the default)",
    )


def _task_field_options(parser: argparse.ArgumentParser, *, create: bool) -> None:
    parser.add_argument("--estimate", type=int, default=25 if create else None)
    parser.add_argument("--plan", choices=["now", "today", "later"], default="today" if create else None)
    parser.add_argument("--status", choices=["open", "waiting", "blocked", "done"], default="open" if create else None)
    parser.add_argument(
        "--category",
        choices=["today", "tomorrow", "this-week", "this-month", "soon"],
        default="today" if create else None,
    )
    parser.add_argument("--due")
    parser.add_argument("--due-timezone")
    parser.add_argument("--project")
    parser.add_argument("--priority", type=int, choices=[1, 2, 3, 4])
    parser.add_argument("--brief")
    parser.add_argument("--next-action")
    parser.add_argument("--closure-condition")
    parser.add_argument("--waiting-on")
    parser.add_argument("--review-date")
    parser.add_argument("--blocker")
    parser.add_argument("--artefact", action="append")
    parser.add_argument("--owner")
    parser.add_argument("--execution-mode", choices=["manual", "supervised", "autonomous"], default="manual" if create else None)
    parser.add_argument("--approval-state", choices=["not-required", "pending", "approved", "rejected"], default="not-required" if create else None)
    parser.add_argument("--recurrence")
    parser.add_argument("--recurrence-rule")
    parser.add_argument("--recurrence-timezone")


def register_cli(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="todo_action")

    list_parser = actions.add_parser("list", aliases=["ls"], help="Show the shared board")
    list_parser.add_argument("--plan", choices=["now", "today", "later"])
    list_parser.add_argument("--status", choices=["open", "waiting", "blocked", "done"])
    list_parser.add_argument("--category", choices=["today", "tomorrow", "this-week", "this-month", "soon"])
    list_parser.add_argument("--project")
    list_parser.add_argument("--owner")
    list_parser.add_argument("--inbox", action="store_true")

    show_parser = actions.add_parser("show", help="Show one task")
    show_parser.add_argument("task_id")
    show_parser.add_argument("--history", action="store_true")
    show_parser.add_argument("--history-limit", type=int, default=100)

    search_parser = actions.add_parser("search", help="Search task context and metadata")
    search_parser.add_argument("query")
    search_parser.add_argument("--plan", choices=["now", "today", "later"])
    search_parser.add_argument("--status", choices=["open", "waiting", "blocked", "done"])
    search_parser.add_argument("--category", choices=["today", "tomorrow", "this-week", "this-month", "soon"])
    search_parser.add_argument("--project")
    search_parser.add_argument("--owner")
    search_parser.add_argument("--inbox", action="store_true")
    search_parser.add_argument("--limit", type=int, default=100)

    agenda_parser = actions.add_parser("agenda", aliases=["start-here"], help="Show the computed Start Here agenda")
    agenda_parser.add_argument("--date")
    agenda_parser.add_argument("--timezone", default="UTC")
    agenda_parser.add_argument("--stale-days", type=int, default=14)
    agenda_parser.add_argument("--limit", type=int, default=50)

    history_parser = actions.add_parser("history", help="Show append-only task history")
    history_parser.add_argument("task_id")
    history_parser.add_argument("--limit", type=int, default=100)

    add_parser = actions.add_parser("add", help="Add a planned task")
    add_parser.add_argument("title")
    _task_field_options(add_parser, create=True)
    add_parser.add_argument("--source")
    add_parser.add_argument("--external-id")
    _mutation_options(add_parser)

    capture_parser = actions.add_parser("capture", help="Capture an untriaged task in Inbox")
    capture_parser.add_argument("title")
    capture_parser.add_argument("--brief")
    capture_parser.add_argument("--owner")
    _mutation_options(capture_parser)

    for name, help_text in (
        ("focus", "Start a task as the single Now item"),
        ("start", "Start a task as the single Now item"),
        ("today", "Plan an open task for Today"),
        ("later", "Move an open task to Later"),
        ("tomorrow", "Move an open task to the Tomorrow category"),
        ("this-week", "Move an open task to the This Week category"),
        ("this-month", "Move an open task to the This Month category"),
        ("soon", "Move an open task to the Soon category"),
        ("reopen", "Reopen and clear waiting/blocking context"),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("task_id")
        _mutation_options(action)

    wait_parser = actions.add_parser("wait", help="Mark a task Waiting")
    wait_parser.add_argument("task_id")
    wait_parser.add_argument("--waiting-on")
    wait_parser.add_argument("--review-date")
    _mutation_options(wait_parser)

    block_parser = actions.add_parser("block", help="Mark a task Blocked")
    block_parser.add_argument("task_id")
    block_parser.add_argument("--blocker")
    _mutation_options(block_parser)

    done_parser = actions.add_parser("done", help="Complete a task and generate its next occurrence")
    done_parser.add_argument("task_id")
    done_parser.add_argument("--closure-note")
    done_parser.add_argument("--evidence", action="append")
    _mutation_options(done_parser)

    due_parser = actions.add_parser("due", help="Set or clear a due date")
    due_parser.add_argument("task_id")
    due_parser.add_argument("date", help="ISO date/datetime, or 'clear'")
    due_parser.add_argument("--timezone")
    _mutation_options(due_parser)

    update_parser = actions.add_parser("update", help="Change task details")
    update_parser.add_argument("task_id")
    update_parser.add_argument("--title")
    _task_field_options(update_parser, create=False)
    update_parser.add_argument("--inbox", choices=["true", "false"])
    _mutation_options(update_parser)

    session_parser = actions.add_parser("session-link", help="Link a Hermes session and optionally start Now")
    session_parser.add_argument("task_id")
    session_parser.add_argument("session_id")
    session_parser.add_argument("--no-start-now", action="store_true")
    _mutation_options(session_parser)

    session_done_parser = actions.add_parser("session-complete", help="Close the task's linked work lifecycle")
    session_done_parser.add_argument("task_id")
    _mutation_options(session_done_parser)

    recurrence_parser = actions.add_parser("recurrence-next", help="Idempotently generate the next occurrence")
    recurrence_parser.add_argument("task_id")
    _mutation_options(recurrence_parser)

    reorder_parser = actions.add_parser("reorder", help="Position a task inside its category")
    reorder_parser.add_argument("task_id")
    reorder_parser.add_argument("--category", choices=["today", "tomorrow", "this-week", "this-month", "soon"])
    reorder_parser.add_argument("--before")
    reorder_parser.add_argument("--after")
    _mutation_options(reorder_parser)

    follow_parser = actions.add_parser("follow-up", help="Atomically complete a task and create its successor")
    follow_parser.add_argument("task_id")
    follow_parser.add_argument("title")
    follow_parser.add_argument("--plan", choices=["now", "today", "later"], default="today")
    follow_parser.add_argument("--status", choices=["open", "waiting", "blocked"], default="open")
    follow_parser.add_argument("--category", choices=["today", "tomorrow", "this-week", "this-month", "soon"], default="today")
    follow_parser.add_argument("--waiting-on")
    follow_parser.add_argument("--review-date")
    follow_parser.add_argument("--blocker")
    follow_parser.add_argument("--closure-note")
    follow_parser.add_argument("--evidence", action="append")
    _mutation_options(follow_parser)

    import_parser = actions.add_parser("import", help="One-time import from a JSON board or task array")
    import_parser.add_argument("path", help="Path to a JSON file")
    _mutation_options(import_parser)

    delete_parser = actions.add_parser("delete", help="Delete a task")
    delete_parser.add_argument("task_id")
    _mutation_options(delete_parser)

    parser.set_defaults(func=todo_command)


def _due_changes(value: str) -> dict[str, str | None]:
    if value == "clear":
        return {"dueDate": None, "dueAt": None, "dueTimezone": None}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return {"dueDate": value, "dueAt": None}
    return {"dueDate": None, "dueAt": value}


def _mutation_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "expected_revision": getattr(args, "expected_revision", None),
        "actor": getattr(args, "actor", None),
        "event_source": "cli",
        "return_board": bool(getattr(args, "board", False))
        or not bool(getattr(args, "compact", False)),
    }


def _task_changes(args: argparse.Namespace) -> dict[str, Any]:
    due_input = getattr(args, "due", None)
    changes = {
        "title": getattr(args, "title", None),
        "estimate": getattr(args, "estimate", None),
        "plan": getattr(args, "plan", None),
        "status": getattr(args, "status", None),
        "category": getattr(args, "category", None),
        "dueTimezone": getattr(args, "due_timezone", None),
        "project": getattr(args, "project", None),
        "priority": getattr(args, "priority", None),
        "brief": getattr(args, "brief", None),
        "nextAction": getattr(args, "next_action", None),
        "closureCondition": getattr(args, "closure_condition", None),
        "waitingOn": getattr(args, "waiting_on", None),
        "reviewDate": getattr(args, "review_date", None),
        "blocker": getattr(args, "blocker", None),
        "owner": getattr(args, "owner", None),
        "executionMode": getattr(args, "execution_mode", None),
        "approvalState": getattr(args, "approval_state", None),
        "recurrence": getattr(args, "recurrence", None),
        "recurrenceRule": getattr(args, "recurrence_rule", None),
        "recurrenceTimezone": getattr(args, "recurrence_timezone", None),
    }
    if getattr(args, "artefact", None) is not None:
        changes["artefacts"] = args.artefact
    if getattr(args, "inbox", None) is not None:
        changes["inbox"] = args.inbox == "true"
    changes = {key: value for key, value in changes.items() if value is not None}
    if due_input is not None:
        changes.update(_due_changes(due_input))
    return changes


def _print_success(output: dict[str, Any]) -> None:
    print(json.dumps({"ok": True, **output}, indent=2))


def todo_command(args: argparse.Namespace) -> int:
    action = getattr(args, "todo_action", None)
    if not action:
        print("Usage: hermes todo {list|show|search|agenda|capture|add|start|update|done|history|...}")
        return 2
    try:
        if action in {"list", "ls"}:
            output = search_tasks(
                None,
                plan=args.plan,
                status=args.status,
                category=getattr(args, "category", None),
                project=args.project,
                owner=args.owner,
                inbox=True if args.inbox else None,
                limit=500,
            )
        elif action == "show":
            output = get_task(args.task_id)
            if args.history:
                output["history"] = get_history(args.task_id, limit=args.history_limit)["events"]
        elif action == "search":
            output = search_tasks(
                args.query,
                plan=args.plan,
                status=args.status,
                category=getattr(args, "category", None),
                project=args.project,
                owner=args.owner,
                inbox=True if args.inbox else None,
                limit=args.limit,
            )
        elif action in {"agenda", "start-here"}:
            output = get_agenda(
                on_date=args.date,
                timezone_name=args.timezone,
                stale_days=args.stale_days,
                limit=args.limit,
            )
        elif action == "history":
            output = get_history(args.task_id, limit=args.limit)
        elif action == "add":
            fields = _task_changes(args)
            fields.pop("title", None)
            output = create_task(
                args.title,
                source=args.source,
                external_id=args.external_id,
                **fields,
                **_mutation_kwargs(args),
            )
        elif action == "capture":
            output = create_task(
                args.title,
                plan="later",
                status="open",
                inbox=True,
                brief=args.brief,
                owner=args.owner,
                **_mutation_kwargs(args),
            )
        elif action in {
            "focus", "start", "today", "later", "tomorrow",
            "this-week", "this-month", "soon", "reopen",
        }:
            plan = {"focus": "now", "start": "now", "today": "today", "later": "later"}.get(action)
            category = {
                "today": "today",
                "later": "soon",
                "tomorrow": "tomorrow",
                "this-week": "this-week",
                "this-month": "this-month",
                "soon": "soon",
            }.get(action)
            changes: dict[str, Any] = {
                "status": "open",
                "waitingOn": None,
                "reviewDate": None,
                "blocker": None,
                "inbox": False,
            }
            if plan:
                changes["plan"] = plan
            if category:
                changes["category"] = category
            output = update_task(args.task_id, changes, **_mutation_kwargs(args))
        elif action == "wait":
            changes = {"status": "waiting", "waitingOn": args.waiting_on, "reviewDate": args.review_date}
            output = update_task(args.task_id, changes, **_mutation_kwargs(args))
        elif action == "block":
            output = update_task(
                args.task_id,
                {"status": "blocked", "blocker": args.blocker},
                **_mutation_kwargs(args),
            )
        elif action == "done":
            changes: dict[str, Any] = {"status": "done"}
            if args.closure_note is not None:
                changes["closureNote"] = args.closure_note
            if args.evidence is not None:
                changes["closureEvidence"] = args.evidence
            output = update_task(
                args.task_id,
                changes,
                **_mutation_kwargs(args),
            )
        elif action == "due":
            changes = _due_changes(args.date)
            if args.timezone is not None:
                changes["dueTimezone"] = args.timezone
            output = update_task(args.task_id, changes, **_mutation_kwargs(args))
        elif action == "update":
            changes = _task_changes(args)
            if args.artefact:
                current = get_task(args.task_id)
                changes["artefacts"] = [*current["task"]["artefacts"], *args.artefact]
                if args.expected_revision is None:
                    args.expected_revision = current["revision"]
            output = update_task(args.task_id, changes, **_mutation_kwargs(args))
        elif action == "session-link":
            output = link_task_session(
                args.task_id,
                args.session_id,
                start_now=not args.no_start_now,
                **_mutation_kwargs(args),
            )
        elif action == "session-complete":
            output = complete_task_session(args.task_id, **_mutation_kwargs(args))
        elif action == "recurrence-next":
            output = generate_next_occurrence(args.task_id, **_mutation_kwargs(args))
        elif action == "reorder":
            output = reorder_task(
                args.task_id,
                category=getattr(args, "category", None),
                before_id=getattr(args, "before", None),
                after_id=getattr(args, "after", None),
                **_mutation_kwargs(args),
            )
        elif action == "follow-up":
            mutation_kwargs = _mutation_kwargs(args)
            if args.closure_note is not None:
                mutation_kwargs["closure_note"] = args.closure_note
            if args.evidence is not None:
                mutation_kwargs["closure_evidence"] = args.evidence
            output = complete_with_follow_up(
                args.task_id,
                {
                    "title": args.title,
                    "plan": args.plan,
                    "status": args.status,
                    "category": args.category,
                    "waitingOn": args.waiting_on,
                    "reviewDate": args.review_date,
                    "blocker": args.blocker,
                },
                **mutation_kwargs,
            )
        elif action == "import":
            payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
            tasks = payload.get("tasks", []) if isinstance(payload, dict) else payload
            if not isinstance(tasks, list):
                raise BoardError("Import JSON must be a task array or an object with a tasks array")
            output = import_tasks(tasks, **_mutation_kwargs(args))
        elif action == "delete":
            output = delete_task(args.task_id, **_mutation_kwargs(args))
        else:
            print(f"Unknown todo action: {action}")
            return 2
    except RevisionConflict as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "revision_conflict",
                    "message": str(exc),
                    "expectedRevision": exc.expected_revision,
                    "currentRevision": exc.current_revision,
                }
            )
        )
        return 3
    except KeyError:
        print(json.dumps({"ok": False, "error": "not_found", "message": "Task not found"}))
        return 1
    except (BoardError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": "invalid_request", "message": str(exc)}))
        return 1
    _print_success(output)
    return 0
