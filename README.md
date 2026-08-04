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

Hermes Todo has two independently installed components. Install the server
plugin first, then the Desktop plugin, into one explicitly selected Hermes
profile. Do not rely on Hermes' sticky active profile.

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

Confirm the Desktop file is at the exact path above, the folder remains
`hermes-todo`, and the file imports only `@hermes/plugin-sdk`, `react`, and
`react/jsx-runtime`. Then run **Reload desktop plugins**.

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
hermes --profile "$PROFILE" todo add "Prepare release notes" --plan today --priority 2 --due 2026-08-07
hermes --profile "$PROFILE" todo focus TASK_ID
hermes --profile "$PROFILE" todo wait TASK_ID
hermes --profile "$PROFILE" todo block TASK_ID
hermes --profile "$PROFILE" todo done TASK_ID
hermes --profile "$PROFILE" todo update TASK_ID --project "Release" --estimate 45
hermes --profile "$PROFILE" todo import ./tasks.json
```

Import accepts either a JSON task array or `{ "tasks": [...] }`. Stable external records should include both `source` and `externalId`; repeated imports skip existing identities.

## Security and data retention

Hermes Desktop disk plugins run with the Desktop renderer's authority; loader
error isolation is not a security sandbox. Review `plugin.js` before installing
it and install only source you trust. A published checksum can confirm package
bytes, but it cannot make unreviewed code safe.

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
