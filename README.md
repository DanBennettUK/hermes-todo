# Hermes Todo

A compact, shared task board for Hermes Desktop and Hermes agents. **Todo** keeps planning position, workflow status, and deadlines independent while using one profile-scoped SQLite database.

## Features

- Planning: **Now**, **Today**, **Later** (with one open Now task)
- Status: **Open**, **Waiting**, **Blocked**, **Done**
- All-day or timed due dates, 5–480 minute estimates, projects, priorities P1–P4, and recurrence notes
- Native Hermes Desktop pane with optimistic writes, polling fallback, filtering, editing, and completion controls
- **Work with Hermes** creates a dedicated session, focuses the task, submits its context, and opens the conversation
- Agent-writable `hermes todo` CLI and namespaced REST backend
- Optional one-time import from plugin-local legacy board storage or a JSON file

## Repository layout

```text
desktop-plugin/hermes-todo/plugin.js       Desktop plugin (plain ESM)
server-plugin/hermes-todo/                  Standalone Hermes server plugin
  dashboard/plugin_api.py                   Namespaced FastAPI router
  dashboard/manifest.json
  hermes_todo_store.py                      SQLite authority
  cli.py                                    CLI implementation
tests/                                      stdlib unittest suite
ARCHITECTURE.md                             architecture and privacy boundary
```

## Install

Install and enable the server plugin from its supported GitHub subdirectory:

```bash
hermes plugins install DanBennettUK/hermes-todo/server-plugin/hermes-todo --enable
```

Desktop installation remains manual. Copy `desktop-plugin/hermes-todo` to the active profile's `$HERMES_HOME/desktop-plugins/hermes-todo`, restart the Hermes backend serving Desktop, then run **Reload desktop plugins** from the Desktop command palette. For the default profile, the Hermes home is usually `~/.hermes`; named profiles use their own profile directory.

The folder, Desktop ID, server manifest name, API path, and data folder must all remain `hermes-todo`. The UI label is **Todo**.

## CLI

```bash
hermes todo list
hermes todo add "Prepare release notes" --plan today --priority 2 --due 2026-08-07
hermes todo focus TASK_ID
hermes todo wait TASK_ID
hermes todo block TASK_ID
hermes todo done TASK_ID
hermes todo update TASK_ID --project "Release" --estimate 45
hermes todo import ./tasks.json
```

Import accepts either a JSON task array or `{ "tasks": [...] }`. Stable external records should include both `source` and `externalId`; repeated imports skip existing identities.

## Security and data retention

The namespaced REST routes inherit authentication and access controls from the Hermes host; the plugin does not implement a second authentication layer or expose a separate listener.

Imported `sourcePayload` metadata is retained only in the profile-local SQLite database, is limited to **65,536 serialized UTF-8 bytes (64 KiB) per task**, and is never included in board responses. Review imports before loading them because payload metadata can still contain private source information. See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Development and verification

No build step or third-party runtime dependency is required for the store/CLI. The API is loaded inside Hermes, which supplies FastAPI and Pydantic.

```bash
PYTHONPATH=server-plugin/hermes-todo python3 -m unittest discover -s tests -v
python3 -m compileall -q server-plugin tests
node --check desktop-plugin/hermes-todo/plugin.js
```

Desktop plugin files are uncompiled ESM: no JSX, bundler, or imports beyond the Hermes SDK, React, and `react/jsx-runtime`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for storage and privacy details.

## Licence

[MIT](LICENSE)
