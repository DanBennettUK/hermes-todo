# Todo Desktop plugin

Choose the profile explicitly and derive its home rather than relying on the
sticky active profile:

```bash
PROFILE=default
PROFILE_HOME="$(dirname "$(hermes --profile "$PROFILE" config path)")"
```

Install this entire directory as
`$PROFILE_HOME/desktop-plugins/hermes-todo`.

The disk loader executes `plugin.js` as one uncompiled ESM module from a blob
URL. Keep the runtime implementation in `plugin.js`; do not add relative
imports such as `./work-prompt.mjs`, because that base URL is not hierarchical.

Install and enable the matching server plugin in the same profile first. Then:

1. select that profile in Hermes Desktop;
2. run **Reload desktop plugins**;
3. if the server plugin was installed after Desktop connected, open connection
   settings and choose **Save and Reconnect** so Desktop stops the cached backend
   and starts a fresh process that mounts the Python API routes;
4. require **Shared with Hermes** and a matching profile-explicit CLI count.

Reloading Desktop plugins only reloads this JavaScript file. Restarting the
messaging gateway is unrelated and does not activate Todo's Desktop API.

The directory and plugin ID must remain `hermes-todo`; the visible label is
**Todo**. See the repository root README for upgrade, verification,
troubleshooting, and rollback instructions.

In v0.2 the capture field always creates an Inbox task. It never fills an empty
Now slot automatically. Use **Start now** or **Work with Hermes** on an open
task to make that explicit. The pane has one Board view; use P1-P4 priority,
Now/Today/Later planning, and due dates to make attention explicit. Linked
active Hermes sessions show a resume action and task detail exposes the durable brief,
closure evidence, recurrence identity, and append-only local history. If a
link request times out after the server committed it, task detail also offers
**Close linked session** for the currently active link; close it explicitly
before starting a replacement.
