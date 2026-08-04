"""FastAPI routes for the shared Hermes Todo board."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from hermes_todo_store import (  # noqa: E402
    BoardError,
    create_task,
    delete_task,
    get_board,
    import_tasks,
    update_task,
)

router = APIRouter()


class TaskCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, strict=True)

    title: str = Field(min_length=1, max_length=500)
    estimate: int = Field(default=25, ge=5, le=480)
    plan: str = "today"
    status: str = "open"
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


class TaskPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, strict=True)

    title: str | None = Field(default=None, min_length=1, max_length=500)
    estimate: int | None = Field(default=None, ge=5, le=480)
    plan: str | None = None
    status: str | None = None
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


class ImportBody(BaseModel):
    tasks: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/board")
def read_board():
    return get_board()


@router.post("/tasks")
def add_task(body: TaskCreate):
    try:
        return create_task(**body.model_dump())
    except BoardError as exc:
        raise _bad_request(exc) from exc


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, body: TaskPatch):
    changes = body.model_dump(exclude_unset=True)
    try:
        return update_task(task_id, changes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except BoardError as exc:
        raise _bad_request(exc) from exc


@router.delete("/tasks/{task_id}")
def remove_task(task_id: str):
    try:
        return delete_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.post("/import")
def import_local_board(body: ImportBody):
    try:
        return import_tasks(body.tasks)
    except BoardError as exc:
        raise _bad_request(exc) from exc
