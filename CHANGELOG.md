# Changelog

All notable changes to Hermes Todo are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-10-16

### Fixed

- The Todo pane crashed on a category row because the move-later control rendered `icons.ArrowDown`, and the Hermes Desktop icon set has no `ArrowDown` export. The control now uses `ArrowBarToDown`. React reported the crash as error 130, with an undefined element type.

### Added

- `HERMES_TODO_PROFILE` selects the Hermes profile for the store, the `hermes todo` command, the `todo_board` tool, and the Desktop pane. Leave it unset to keep using the active Hermes home. See the README section "Pin Todo to one Hermes profile".

## [0.2.0] - 2026-10-16

### Added

- Task categories: Today, Tomorrow, This Week, This Month, and Soon.
- Positional ordering inside a category and across categories. Neighbours are checked, and `expectedRevision` detects a concurrent edit. The routes are `POST /tasks/{id}/reorder` and `hermes todo reorder`.
- Drag-and-drop category buckets in the Todo pane.
- Parent-task subtask checklists in the store, the REST API, the `todo_board` tool, and the desktop **Subs** tab.
- The `todo_board` agent tool. Its actions are `list`, `get`, `create`, `update`, `reorder`, `start`, `done`, `subtask_create`, `subtask_update`, `subtask_delete`, `subtask_reorder`, and `subtask_sort`. It cannot delete a parent task.
- A tabbed desktop editor (**Work**, **Plan**, **Wait**, **Subs**, **More**) that saves while you edit.
- Redesigned task cards with priority pills, one header row, two-click completion, and a **Show Todo** command.
- GitHub Actions tests on Linux and Windows.
- `scripts/build_release_zips.py`, which builds the full archive and the Desktop-only archive, each with a SHA-256 sidecar. The script does not tag or publish a release.

### Changed

- The database migrates to schema v7 the first time 0.2.0 opens it. The migration is one-way. Back up `todo.sqlite3` before you upgrade. Existing tasks are kept. A task whose plan is Later gets the Soon category. The plan value stays Later.
- Request bodies reject unknown fields with HTTP 422. Older servers ignored those fields.
- The desktop editor finishes a pending save on close, and a stale revision is reported as a conflict.
- A task `position` on create, update, or import must be from -1e15 through 1e15, including both ends.

### Fixed

- On Windows, the store closes its file handles and the test suite passes.
- Subtask positions stay unique when a midpoint has no number left between its neighbours. Recurrence and import return the copied subtasks.
- Positional reorder checks its anchors before it writes.
- `todo_board` reports `Unknown action` when the action is not one of the twelve. It no longer says that an unknown action requires `task_id`.

Apostol Apostolov contributed the category board, positional ordering, drag-and-drop, the card redesign, and the subtask checklists.

## [0.1.2] - 2026-08-04

Published release. See the [v0.1.2 release notes](https://github.com/DanBennettUK/hermes-todo/releases/tag/v0.1.2).
