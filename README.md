# Hermes Todo

A compact, shared task board for Hermes Desktop and Hermes agents. **Todo** keeps planning position, workflow status, and deadlines independent while using one profile-scoped SQLite database.

## Features

- Planning: **Now**, **Today**, **Later** (with one open Now task)
- Status: **Open**, **Waiting**, **Blocked**, **Done**
- Inbox capture that never claims **Now** without an explicit **Start now** action
- Durable brief, next action, closure condition, waiting/blocking context, review date, owner, approval, and bounded artefact/evidence fields
- One Board view with explicit Now/Today/Later planning, due-date grouping, and P1-P4 priority
- Append-only machine-readable task history and truthful closure notes/evidence
- **Work with Hermes** persists the linked stored-session ID and resumes it instead of creating a duplicate
- All-day or timed due dates, 5–480 minute estimates, projects, priorities P1–P4, legacy recurrence notes, and deterministic recurrence occurrences
- Agent-writable `hermes todo` CLI and namespaced REST backend with task reads, search, agenda, backward-compatible board mutation envelopes, compact opt-in results, and revision conflicts
- Optional one-time import from plugin-local legacy board storage or a JSON file

### Work-session re-entry contract

Keep task titles concise: they are the board label, not the handover document.
Use the task's durable fields for the context that must survive into a new work
session:

- **Brief and decisions** for background, constraints and conclusions already reached
- **Next action** for the smallest live move
- **Closure condition** for what proves the task is complete
- **Artefacts** for meeting notes, source pages, files or other links to inspect
- **Waiting/blocking context** when progress depends on another person or condition

**Work with Hermes** includes those work-relevant fields in the initial prompt.
It deliberately does not copy private `sourcePayload` metadata or append-only
closure history into the prompt. The title can therefore stay readable while
the session still receives the useful handover.

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

Hermes Todo has two independently installed components. Install the server
plugin first, then the Desktop plugin, into one explicitly selected Hermes
profile. Do not rely on Hermes' sticky active profile.

The manifests in this source tree identify the v0.2.0 product slice. The
versioned download examples below intentionally remain on the last published
v0.1.2 release; do not substitute an unpublished v0.2.0 archive or checksum.

### Quick install with your agent

If your agent can install Hermes plugins, tell it:

```text
Install Hermes Todo v0.1.2 from https://github.com/DanBennettUK/hermes-todo/releases/tag/v0.1.2 into the `default` Hermes profile. Use the release bundle, install both the server and Desktop components, and verify that the Todo pane reports Shared with Hermes.
```

Replace `default` with the name of your selected Hermes profile when needed.
The manual steps below explain the same installation if you prefer to run it
yourself.

Set the target once for every command below. Use `default` literally for the
default profile, or replace it with a named profile:

```bash
PROFILE=default
PROFILE_HOME="$(dirname "$(hermes --profile "$PROFILE" config path)")"
```

PowerShell equivalent:

```powershell
$HermesProfile = "default"
$ProfileHome = Split-Path (hermes --profile $HermesProfile config path)
```

### 1. Install the server plugin

```bash
hermes --profile "$PROFILE" plugins install --no-enable \
  DanBennettUK/hermes-todo/server-plugin/hermes-todo
```

The GitHub subdirectory installer copies the plugin into the profile. It does
not retain a Git checkout, so later upgrades use `plugins install --force`
rather than `plugins update`. The current Hermes installer also clones the
repository's default branch; it does not pin subdirectory installs to a release
tag. This is a convenience install from current `main`, not an immutable release
install. Review the copied files and confirm
`$PROFILE_HOME/plugins/hermes-todo/plugin.yaml` before enabling them:

```bash
hermes --profile "$PROFILE" plugins enable hermes-todo
```

For a reproducible install, use the versioned release bundle and its published
SHA-256 checksum instead. The bundle contains both components from one tag.

### Reproducible release install

Download both `hermes-todo-v0.1.2.zip` and
`hermes-todo-v0.1.2.zip.sha256` from the release, then verify and unpack them:

```bash
sha256sum -c hermes-todo-v0.1.2.zip.sha256
python3 -m zipfile -e hermes-todo-v0.1.2.zip .
BUNDLE_DIR="$PWD/hermes-todo-v0.1.2"
```

On Windows, compare `Get-FileHash hermes-todo-v0.1.2.zip -Algorithm SHA256`
with the published `.sha256` value, then install with:

```powershell
Expand-Archive -Path hermes-todo-v0.1.2.zip -DestinationPath .
$BundleDir = Join-Path $PWD "hermes-todo-v0.1.2"
New-Item -ItemType Directory -Force (Join-Path $ProfileHome "plugins")
New-Item -ItemType Directory -Force (Join-Path $ProfileHome "desktop-plugins")
Copy-Item -Recurse (Join-Path $BundleDir "server-plugin/hermes-todo") `
  (Join-Path $ProfileHome "plugins/hermes-todo")
Copy-Item -Recurse (Join-Path $BundleDir "desktop-plugin/hermes-todo") `
  (Join-Path $ProfileHome "desktop-plugins/hermes-todo")
hermes --profile $HermesProfile plugins enable hermes-todo
```

Review the unpacked source, then copy both components into the explicit profile
and enable the server plugin:

```bash
mkdir -p "$PROFILE_HOME/plugins" "$PROFILE_HOME/desktop-plugins"
cp -a "$BUNDLE_DIR/server-plugin/hermes-todo" \
  "$PROFILE_HOME/plugins/hermes-todo"
cp -a "$BUNDLE_DIR/desktop-plugin/hermes-todo" \
  "$PROFILE_HOME/desktop-plugins/hermes-todo"
hermes --profile "$PROFILE" plugins enable hermes-todo
```

### 2. Install the Desktop plugin

Copy the complete `desktop-plugin/hermes-todo` directory from the same source or
release bundle to:

```text
$PROFILE_HOME/desktop-plugins/hermes-todo/plugin.js
```

The destination directory, Desktop plugin ID, server plugin name, API
namespace, and data directory must all remain `hermes-todo`. The visible label
is **Todo**.

### 3. Activate both components

1. In Hermes Desktop, select the same profile used for the server-plugin
   installation.
2. Run **Reload desktop plugins** from the command palette. This reloads the
   JavaScript UI only.
3. If that profile already had a Desktop backend running before the server
   plugin was installed or enabled, open Desktop connection settings and choose
   **Save and Reconnect**. Hermes Desktop invalidates the cached backend, waits
   for it to exit, and starts a fresh route-owning process. Merely reloading the
   renderer or reconnecting to the same process is insufficient.
4. Open Todo and require **Shared with Hermes** before using it. That successful
   route plus the matching CLI count below is the activation proof.

Do **not** restart the messaging gateway for a Todo API 404. The gateway and the
Desktop profile backend are separate processes; restarting the gateway does
not reload Desktop plugin routes.

### 4. Verify

The Todo pane should report **Shared with Hermes** and display the same board
as the CLI. To verify the count without printing task contents:

```bash
hermes --profile "$PROFILE" todo list | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print({"tasks": len(d["tasks"]), "revision": d["revision"]})'
```

## Upgrade

Before an upgrade, preserve an executable copy of both installed components.
The data directory is separate and must not be copied, moved, or deleted:

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$PROFILE_HOME/backups/hermes-todo-code-$STAMP"
mkdir -p "$BACKUP_DIR"
cp -a "$PROFILE_HOME/plugins/hermes-todo" "$BACKUP_DIR/server"
cp -a "$PROFILE_HOME/desktop-plugins/hermes-todo" "$BACKUP_DIR/desktop"
```

For the mutable convenience path, disable the plugin, replace the copied server
code, review it, then enable it again:

```bash
hermes --profile "$PROFILE" plugins disable hermes-todo
hermes --profile "$PROFILE" plugins install --force --no-enable \
  DanBennettUK/hermes-todo/server-plugin/hermes-todo
hermes --profile "$PROFILE" plugins enable hermes-todo
```

Replace the Desktop `hermes-todo` directory from the same reviewed source. Run
**Reload desktop plugins**, then use **Save and Reconnect** whenever Python
backend code changed or the server plugin was newly enabled. Require **Shared
with Hermes** and confirm the CLI count before resuming writes.

`hermes plugins update hermes-todo` is not supported for this GitHub
subdirectory install because the installed copy has no `.git` directory.
The reinstall command fetches the current default branch, not an immutable
release tag.

## Troubleshooting

### Offline with a headless-backend 404

An error such as `Headless backend (hermes serve): Web UI disabled` does not
mean Todo needs the browser dashboard. It means the authenticated request
reached a Desktop backend that did not mount the Todo route.

1. Confirm `hermes-todo` is enabled in the same profile selected in Desktop:
   `hermes --profile "$PROFILE" plugins list --plain --no-bundled`.
2. Confirm the server manifest exists at
   `$PROFILE_HOME/plugins/hermes-todo/dashboard/manifest.json`.
3. In Desktop connection settings, choose **Save and Reconnect** so Desktop
   stops the cached backend and starts a fresh one for that profile.
4. Run **Reload desktop plugins** and reopen Todo.

Do not rerun an import or migration to troubleshoot this error. Database
presence and CLI success do not prove an already-running Desktop backend loaded
the API route.

If Desktop still sees a backend process but cannot connect to it after **Save
and Reconnect**, do not kill arbitrary Hermes processes or force-restart the
messaging gateway. Capture the Desktop error and run
`hermes logs gui -f`; a process that remains alive after losing its listener is
a Hermes Desktop lifecycle fault, not a Todo database fault.

### Plugin does not appear

Confirm the Desktop files are at the exact path above, the folder remains
`hermes-todo`, and the plugin uses only the Hermes SDK, React, the JSX runtime,
and its inline re-entry prompt formatter. Disk plugins must remain a single
plain ESM file because Hermes Desktop evaluates them from a blob URL; relative
imports such as `./work-prompt.mjs` cannot resolve there. Then run **Reload
desktop plugins**.

## Disable or roll back

Disable the Desktop plugin in **Settings → Plugins**, then disable the server
plugin in the explicitly selected profile:

```bash
hermes --profile "$PROFILE" plugins disable hermes-todo
```

Do not delete `$PROFILE_HOME/hermes-todo/todo.sqlite3`. Plugin code and data are
separate, so the board remains available for re-enabling or diagnosis.

To roll back, restore the exact pre-upgrade code snapshot while retaining the
failed version for diagnosis:

```bash
FAILED_SUFFIX="$(date -u +%Y%m%dT%H%M%SZ)"
mv "$PROFILE_HOME/plugins/hermes-todo" \
  "$PROFILE_HOME/plugins/hermes-todo.failed-$FAILED_SUFFIX"
mv "$PROFILE_HOME/desktop-plugins/hermes-todo" \
  "$PROFILE_HOME/desktop-plugins/hermes-todo.failed-$FAILED_SUFFIX"
cp -a "$BACKUP_DIR/server" "$PROFILE_HOME/plugins/hermes-todo"
cp -a "$BACKUP_DIR/desktop" "$PROFILE_HOME/desktop-plugins/hermes-todo"
diff -qr "$BACKUP_DIR/server" "$PROFILE_HOME/plugins/hermes-todo"
diff -qr "$BACKUP_DIR/desktop" "$PROFILE_HOME/desktop-plugins/hermes-todo"
hermes --profile "$PROFILE" plugins enable hermes-todo
```

On Windows, use `Copy-Item -Recurse` for code snapshots and `Move-Item` to
retain the failed version; keep the same source and destination directories.

Then run **Reload desktop plugins**, choose **Save and Reconnect**, require
**Shared with Hermes**, and verify the CLI count before resuming writes. A
versioned release bundle can be restored in the same way after verifying its
published SHA-256 checksum.

## CLI

```bash
hermes --profile "$PROFILE" todo list
hermes --profile "$PROFILE" todo capture "Prepare release notes" --brief "Decision log and context"
hermes --profile "$PROFILE" todo start TASK_ID --expected-revision REVISION
hermes --profile "$PROFILE" todo agenda --timezone Europe/Amsterdam
hermes --profile "$PROFILE" todo show TASK_ID --history
hermes --profile "$PROFILE" todo search "release" --owner Dan
hermes --profile "$PROFILE" todo wait TASK_ID --waiting-on "Reviewer" --review-date 2026-08-12
hermes --profile "$PROFILE" todo block TASK_ID --blocker "Approval required"
hermes --profile "$PROFILE" todo done TASK_ID --closure-note "Verified locally" --evidence tests/test_store.py
hermes --profile "$PROFILE" todo add "Monthly close" --due 2026-08-31 --recurrence-rule monthly --recurrence-timezone Europe/Amsterdam
hermes --profile "$PROFILE" todo update TASK_ID --next-action "Run the focused suite" --artefact tests/test_store.py
hermes --profile "$PROFILE" todo session-link TASK_ID STORED_SESSION_ID --expected-revision REVISION
hermes --profile "$PROFILE" todo follow-up TASK_ID "Check response" --status waiting --waiting-on Reviewer
hermes --profile "$PROFILE" todo import ./tasks.json
```

Read commands and mutation commands emit explicit JSON objects. Mutations default to the backward-compatible full-board envelope with `tasks` and the new `revision`; recurrence and follow-up operations also retain their `generatedTask` or `followUpTask` metadata. Pass `--compact` for a result containing the changed `task` and any `affectedTasks`, `generatedTask`, or `followUpTask`. The existing `--board` spelling remains available as an explicit request for the default board envelope. Pass `--expected-revision` to protect a read/modify/write sequence; a stale write exits with code 3 and an `error: "revision_conflict"` object without changing the board.

Import accepts either a JSON task array or `{ "tasks": [...] }`. Stable external records should include both `source` and `externalId`; repeated imports skip existing identities. Imported `createdAt`, `updatedAt`, and (for Done tasks) `completedAt` are preserved. Unknown imported fields are merged into the private, non-returned source payload without replacing an existing `sourcePayload.legacyFields` mapping.

### Recurrence rules

The free-text `recurrence` field remains a display note and is never treated as executable. `recurrenceRule` is deterministic and supports only:

- `daily`
- `weekdays`
- `weekly`
- `monthly` (the day is clamped to the destination month's last day)
- `every:<n>d`, where `n` is 1–365, for example `every:14d`

An executable rule requires `dueDate` or `dueAt`. `recurrenceTimezone` is an IANA timezone and defaults to the timed due timezone or UTC. Completing an occurrence preserves it as Done and idempotently creates the next occurrence with one stable `seriesId` and a unique `occurrenceId` such as `<seriesId>:2`. Imports that provide recurrence identity must provide `seriesId`, `occurrenceId`, and `occurrenceNumber` together, with `occurrenceId` exactly `<seriesId>:<occurrenceNumber>`; when none are supplied, an executable recurrence rule creates the initial identity.

### REST contract

The namespaced API exposes `GET /board`, `GET /tasks`, `GET /tasks/{id}`, `GET /tasks/{id}/history`, and `GET /agenda`. Mutations default to the backward-compatible full-board envelope. Add `?envelope=result` to opt into the compact mutation result; `?envelope=board` remains a compatible explicit board request. Mutation bodies accept `expectedRevision`; delete accepts it as a query parameter. Conflicts return HTTP 409 with `expectedRevision` and `currentRevision`.

Agenda conversion interprets a naive `dueAt` in the task's `dueTimezone` before converting it to the requested agenda timezone; invalid legacy timezone metadata safely falls back to the agenda timezone. A waiting task receives the `waiting_review` reason only when its `reviewDate` is today or overdue.

Dedicated operations link or idempotently complete a work session, idempotently generate the next recurrence, and atomically complete a task with a follow-up. Omitting closure fields from complete-with-follow-up preserves existing closure data; explicit null or empty values clear it. They remain local Todo state changes: a closure note or evidence path does not claim that an external delivery was verified.

## Security and data retention

Hermes Desktop disk plugins run with the Desktop renderer's authority; loader
error isolation is not a security sandbox. Review `plugin.js` before installing
it and install only source you trust. A published checksum can confirm package
bytes, but it cannot make unreviewed code safe.

The namespaced REST routes inherit authentication and access controls from the Hermes host; the plugin does not implement a second authentication layer or expose a separate listener.

Imported `sourcePayload` metadata is retained only in the profile-local SQLite database, is limited to **65,536 serialized UTF-8 bytes (64 KiB) per task**, and is never included in board responses. Review imports before loading them because payload metadata can still contain private source information. See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## Development and verification

No build step or third-party runtime dependency is required for the store/CLI. The API is loaded inside Hermes, which supplies FastAPI and Pydantic. Python's standard-library timezone database must contain the selected IANA recurrence/agenda timezone.

```bash
PYTHONPATH=server-plugin/hermes-todo python3 -m unittest discover -s tests -v
python3 -m compileall -q server-plugin tests
node --check desktop-plugin/hermes-todo/plugin.js
```

Desktop plugin files are uncompiled ESM: no JSX, bundler, or imports beyond the Hermes SDK, React, and `react/jsx-runtime`.

See [ARCHITECTURE.md](ARCHITECTURE.md) for storage and privacy details.

## Licence

[MIT](LICENSE)
