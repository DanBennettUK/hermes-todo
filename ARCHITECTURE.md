# Architecture and privacy

Hermes Todo has two independently installed halves with one stable namespace: `hermes-todo`.

- **Desktop:** `desktop-plugin/hermes-todo/plugin.js` is plain ESM loaded by Hermes Desktop. It imports only `@hermes/plugin-sdk`, `react`, and `react/jsx-runtime`. It polls its own namespaced REST API and uses Hermes gateway RPC only after an explicit **Work with Hermes** click.
- **Server:** `server-plugin/hermes-todo/` is a standalone Hermes plugin. Its FastAPI router and `hermes todo` CLI both call the same stdlib SQLite store. The store module has a plugin-specific name so it cannot collide with another standalone plugin's Python modules.
- **Data:** each active Hermes home/profile owns `$HERMES_HOME/hermes-todo/todo.sqlite3`. The repository contains no database or user data. SQLite WAL plus immediate write transactions make Desktop and agent writes share one authority; a partial unique index enforces one open **Now** task.

Task titles, projects, due values, recurrence notes, and optional import metadata remain local to the Hermes server/profile. The plugin has no analytics, telemetry, cloud sync, or third-party API integration. Its REST routes inherit the Hermes host's authentication and access controls. Desktop local-storage import and CLI JSON import are user-initiated, insert-only, idempotent by task ID or `(source, externalId)`, and never overwrite an existing task. Imported `sourcePayload` can contain private source metadata, is limited to 65,536 serialized UTF-8 bytes per task, and is retained in SQLite without being returned in board payloads.
