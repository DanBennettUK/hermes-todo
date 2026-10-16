# Security policy

## Supported versions

Version 0.2.1 is the supported release. Version 0.1.2 and older are not.
Security fixes are assessed for 0.2.1.

Upgrading from 0.1.2 migrates `todo.sqlite3` to schema v7. That migration is
one-way. Back up the database before you upgrade. The upgrade section in
[README.md](README.md) has the copy commands.

## Reporting a vulnerability

Report a suspected vulnerability privately through the repository's GitHub
security-advisory form. Include reproduction steps, the affected components,
the impact, and any mitigation you suggest. Do not open a public issue for an
unpatched vulnerability.

Reports are acknowledged and assessed as promptly as practical. Allow time for
investigation and a coordinated fix before public disclosure.

## Scope

The plugin relies on the Hermes host for authentication and transport security.
Reports about authorization boundaries, unsafe input handling, local data
exposure, or unintended network access are in scope.

Hermes Desktop disk plugins run with the renderer's authority. They are not
sandboxed from the host application. Review `plugin.js` and check the release
checksums before you install. A checksum shows that the package bytes match.
It does not make the code safe.

The profile database is `$PROFILE_HOME/hermes-todo/todo.sqlite3`. On platforms
that honor Unix permission bits, the directory mode is `0700` and the database
files are mode `0600`.

The `todo_board` agent tool can list, create, update, reorder, and complete
tasks, and it can change subtasks. It cannot delete a parent task.
