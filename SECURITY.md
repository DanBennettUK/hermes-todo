# Security policy

## Supported version

The v0.2.0 source tree is under development and has not been published. The
latest published release is v0.1.2; security fixes are assessed for that
release and the unpublished development tree as appropriate.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through the repository's GitHub security-advisory form. Include reproduction steps, affected components, impact, and any suggested mitigation. Do not open a public issue for an unpatched vulnerability.

Reports will be acknowledged and assessed as promptly as practical. Please allow time for investigation and a coordinated fix before public disclosure.

## Scope

The plugin relies on the Hermes host for authentication and transport security. Reports involving authorization boundaries, unsafe input handling, local data exposure, or unintended network access are in scope.

Hermes Desktop disk plugins execute with the renderer's authority and are not
sandboxed from the host application. Users should review `plugin.js` and verify
release checksums before installation. Checksums establish package integrity,
not code safety.
