"""FastAPI routes for the shared Hermes Todo board."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from hermes_todo_store import (  # noqa: E402
    BoardError,
    RevisionConflict,
    complete_task_session,
    complete_with_follow_up,
    create_task,
    delete_task,
    generate_next_occurrence,
    get_agenda,
    get_board,
    get_history,
    get_task,
    import_tasks,
    link_task_session,
    search_tasks,
    update_task,
)

router = APIRouter()


class StrictModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, strict=True, extra="forbid")


class TaskCreateFields(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    estimate: int = Field(default=25, ge=5, le=480)
    plan: str = "today"
    status: str = "open"
    category: str = "today"
    due_date: str | None = Field(default=None, alias="dueDate")
    due_at: str | None = Field(default=None, alias="dueAt")
    due_timezone: str | None = Field(default=None, alias="dueTimezone", max_length=100)
    due_language: str | None = Field(default=None, alias="dueLanguage", max_length=50)
    source: str | None = Field(default=None, max_length=100)
    external_id: str | None = Field(default=None, alias="externalId", max_length=200)
    project: str | None = Field(default=None, max_length=500)
    priority: int | None = Field(default=None, ge=1, le=4)
    recurrence: str | None = Field(default=None, max_length=500)
    source_updated_at: str | None = Field(default=None, alias="sourceUpdatedAt", max_length=100)
    source_payload: Any = Field(default=None, alias="sourcePayload")
    lane: str | None = None
    brief: str | None = Field(default=None, max_length=8000)
    next_action: str | None = Field(default=None, alias="nextAction", max_length=2000)
    closure_condition: str | None = Field(default=None, alias="closureCondition", max_length=4000)
    waiting_on: str | None = Field(default=None, alias="waitingOn", max_length=1000)
    review_date: str | None = Field(default=None, alias="reviewDate")
    blocker: str | None = Field(default=None, max_length=2000)
    artefacts: list[str] = Field(default_factory=list, max_length=20)
    owner: str | None = Field(default=None, max_length=200)
    execution_mode: str = Field(default="manual", alias="executionMode")
    approval_state: str = Field(default="not-required", alias="approvalState")
    inbox: bool = False
    closure_note: str | None = Field(default=None, alias="closureNote", max_length=4000)
    closure_evidence: list[str] = Field(default_factory=list, alias="closureEvidence", max_length=20)
    recurrence_rule: str | None = Field(default=None, alias="recurrenceRule", max_length=50)
    recurrence_timezone: str | None = Field(default=None, alias="recurrenceTimezone", max_length=100)


class TaskCreate(TaskCreateFields):
    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=0)
    actor: str | None = Field(default=None, max_length=200)
    event_source: str | None = Field(default=None, alias="eventSource", max_length=100)


class TaskPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    estimate: int | None = Field(default=None, ge=5, le=480)
    plan: str | None = None
    status: str | None = None
    category: str | None = None
    due_date: str | None = Field(default=None, alias="dueDate")
    due_at: str | None = Field(default=None, alias="dueAt")
    due_timezone: str | None = Field(default=None, alias="dueTimezone", max_length=100)
    due_language: str | None = Field(default=None, alias="dueLanguage", max_length=50)
    source: str | None = Field(default=None, max_length=100)
    external_id: str | None = Field(default=None, alias="externalId", max_length=200)
    project: str | None = Field(default=None, max_length=500)
    priority: int | None = Field(default=None, ge=1, le=4)
    recurrence: str | None = Field(default=None, max_length=500)
    source_updated_at: str | None = Field(default=None, alias="sourceUpdatedAt", max_length=100)
    source_payload: Any = Field(default=None, alias="sourcePayload")
    lane: str | None = None
    brief: str | None = Field(default=None, max_length=8000)
    next_action: str | None = Field(default=None, alias="nextAction", max_length=2000)
    closure_condition: str | None = Field(default=None, alias="closureCondition", max_length=4000)
    waiting_on: str | None = Field(default=None, alias="waitingOn", max_length=1000)
    review_date: str | None = Field(default=None, alias="reviewDate")
    blocker: str | None = Field(default=None, max_length=2000)
    artefacts: list[str] | None = Field(default=None, max_length=20)
    owner: str | None = Field(default=None, max_length=200)
    execution_mode: str | None = Field(default=None, alias="executionMode")
    approval_state: str | None = Field(default=None, alias="approvalState")
    inbox: bool | None = None
    closure_note: str | None = Field(default=None, alias="closureNote", max_length=4000)
    closure_evidence: list[str] | None = Field(default=None, alias="closureEvidence", max_length=20)
    recurrence_rule: str | None = Field(default=None, alias="recurrenceRule", max_length=50)
    recurrence_timezone: str | None = Field(default=None, alias="recurrenceTimezone", max_length=100)
    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=0)
    actor: str | None = Field(default=None, max_length=200)
    event_source: str | None = Field(default=None, alias="eventSource", max_length=100)


class ImportBody(StrictModel):
    tasks: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=0)
    actor: str | None = Field(default=None, max_length=200)
    event_source: str | None = Field(default=None, alias="eventSource", max_length=100)


class SessionLinkBody(StrictModel):
    session_id: str = Field(alias="sessionId", min_length=1, max_length=300)
    start_now: bool = Field(default=True, alias="startNow")
    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=0)
    actor: str | None = Field(default=None, max_length=200)
    event_source: str | None = Field(default=None, alias="eventSource", max_length=100)


class MutationBody(StrictModel):
    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=0)
    actor: str | None = Field(default=None, max_length=200)
    event_source: str | None = Field(default=None, alias="eventSource", max_length=100)


class CompleteFollowUpBody(MutationBody):
    follow_up: TaskCreateFields = Field(alias="followUp")
    closure_note: str | None = Field(default=None, alias="closureNote", max_length=4000)
    closure_evidence: list[str] | None = Field(
        default=None, alias="closureEvidence", max_length=20
    )


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _conflict(exc: RevisionConflict) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": "revision_conflict",
            "message": str(exc),
            "expectedRevision": exc.expected_revision,
            "currentRevision": exc.current_revision,
        },
    )


def _envelope(value: str) -> bool:
    if value not in {"result", "board"}:
        raise HTTPException(status_code=400, detail="Envelope must be result or board")
    return value == "board"


def _meta(payload: dict[str, Any]) -> tuple[int | None, str | None, str]:
    expected_revision = payload.pop("expected_revision", None)
    actor = payload.pop("actor", None)
    event_source = payload.pop("event_source", None) or "api"
    return expected_revision, actor, event_source


@router.get("/board")
def read_board():
    return get_board()


@router.get("/agenda")
def read_agenda(
    on_date: str | None = Query(default=None, alias="date"),
    timezone_name: str = Query(default="UTC", alias="timezone"),
    stale_days: int = Query(default=14, alias="staleDays", ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
):
    try:
        return get_agenda(
            on_date=on_date,
            timezone_name=timezone_name,
            stale_days=stale_days,
            limit=limit,
        )
    except BoardError as exc:
        raise _bad_request(exc) from exc


@router.get("/tasks")
def read_tasks(
    q: str | None = None,
    plan: str | None = None,
    status: str | None = None,
    category: str | None = None,
    project: str | None = None,
    owner: str | None = None,
    inbox: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    try:
        return search_tasks(
            q,
            plan=plan,
            status=status,
            category=category,
            project=project,
            owner=owner,
            inbox=inbox,
            limit=limit
        )
    except BoardError as exc:
        raise _bad_request(exc) from exc


@router.get("/tasks/{task_id}")
def read_task(task_id: str):
    try:
        return get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.get("/tasks/{task_id}/history")
def read_task_history(task_id: str, limit: int = Query(default=100, ge=1, le=200)):
    try:
        return get_history(task_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except BoardError as exc:
        raise _bad_request(exc) from exc


@router.post("/tasks")
def add_task(body: TaskCreate, envelope: str = "board"):
    payload = body.model_dump()
    expected_revision, actor, event_source = _meta(payload)
    try:
        return create_task(
            **payload,
            expected_revision=expected_revision,
            actor=actor,
            event_source=event_source,
            return_board=_envelope(envelope),
        )
    except RevisionConflict as exc:
        raise _conflict(exc) from exc
    except BoardError as exc:
        raise _bad_request(exc) from exc


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, body: TaskPatch, envelope: str = "board"):
    changes = body.model_dump(exclude_unset=True)
    expected_revision, actor, event_source = _meta(changes)
    try:
        return update_task(
            task_id,
            changes,
            expected_revision=expected_revision,
            actor=actor,
            event_source=event_source,
            return_board=_envelope(envelope),
        )
    except RevisionConflict as exc:
        raise _conflict(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except BoardError as exc:
        raise _bad_request(exc) from exc


@router.delete("/tasks/{task_id}")
def remove_task(
    task_id: str,
    expected_revision: int | None = Query(default=None, alias="expectedRevision", ge=0),
    envelope: str = "board",
):
    try:
        return delete_task(
            task_id,
            expected_revision=expected_revision,
            event_source="api",
            return_board=_envelope(envelope),
        )
    except RevisionConflict as exc:
        raise _conflict(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.post("/tasks/{task_id}/session")
def link_session(task_id: str, body: SessionLinkBody, envelope: str = "board"):
    payload = body.model_dump()
    expected_revision, actor, event_source = _meta(payload)
    try:
        return link_task_session(
            task_id,
            payload["session_id"],
            start_now=payload["start_now"],
            expected_revision=expected_revision,
            actor=actor,
            event_source=event_source,
            return_board=_envelope(envelope),
        )
    except RevisionConflict as exc:
        raise _conflict(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except BoardError as exc:
        raise _bad_request(exc) from exc


@router.post("/tasks/{task_id}/session/complete")
def finish_session(task_id: str, body: MutationBody, envelope: str = "board"):
    payload = body.model_dump()
    expected_revision, actor, event_source = _meta(payload)
    try:
        return complete_task_session(
            task_id,
            expected_revision=expected_revision,
            actor=actor,
            event_source=event_source,
            return_board=_envelope(envelope),
        )
    except RevisionConflict as exc:
        raise _conflict(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.post("/tasks/{task_id}/recurrence/next")
def next_occurrence(task_id: str, body: MutationBody, envelope: str = "board"):
    payload = body.model_dump()
    expected_revision, actor, event_source = _meta(payload)
    try:
        return generate_next_occurrence(
            task_id,
            expected_revision=expected_revision,
            actor=actor,
            event_source=event_source,
            return_board=_envelope(envelope),
        )
    except RevisionConflict as exc:
        raise _conflict(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except BoardError as exc:
        raise _bad_request(exc) from exc


@router.post("/tasks/{task_id}/complete-with-follow-up")
def complete_and_follow_up(
    task_id: str, body: CompleteFollowUpBody, envelope: str = "board"
):
    payload = body.model_dump(exclude_unset=True)
    expected_revision, actor, event_source = _meta(payload)
    follow_up = payload.pop("follow_up")
    try:
        return complete_with_follow_up(
            task_id,
            follow_up,
            expected_revision=expected_revision,
            actor=actor,
            event_source=event_source,
            return_board=_envelope(envelope),
            **payload,
        )
    except RevisionConflict as exc:
        raise _conflict(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except BoardError as exc:
        raise _bad_request(exc) from exc


@router.post("/import")
def import_local_board(body: ImportBody, envelope: str = "board"):
    payload = body.model_dump()
    expected_revision, actor, event_source = _meta(payload)
    try:
        return import_tasks(
            payload["tasks"],
            expected_revision=expected_revision,
            actor=actor,
            event_source=event_source,
            return_board=_envelope(envelope),
        )
    except RevisionConflict as exc:
        raise _conflict(exc) from exc
    except BoardError as exc:
        raise _bad_request(exc) from exc
