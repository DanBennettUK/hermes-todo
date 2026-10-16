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

To keep this pane on one profile while Desktop is connected to another, set `HERMES_TODO_PROFILE` for the Hermes process that serves the pane, then choose **Save and Reconnect**. The footer shows that profile id after **Shared with Hermes**. Leave the variable unset to keep the active profile. The repository README has the id rules.

Reloading Desktop plugins only reloads this JavaScript file. Restarting the
messaging gateway is unrelated and does not activate Todo's Desktop API.

The directory and plugin ID must remain `hermes-todo`; the visible label is
**Todo**. See the repository root README for upgrade, verification,
troubleshooting, and rollback instructions.

In v0.2 the capture field always creates an Inbox task. It never fills an empty
Now slot automatically. Use **Start now** or **Work with Hermes** on an open
task to make that explicit. The pane has one Board view. Category buckets are
**Today**, **Tomorrow**, **This Week**, **This Month**, and **Soon**, and you
can drag a card between them. Plan is still **Now**, **Today**, or **Later**,
with at most one open Now task. The task editor is tabbed and saves as you
edit. Completion asks for a second click. **Show Todo** in the command palette
opens the pane. Linked
active Hermes sessions show a resume action and task detail exposes the durable brief,
closure evidence, recurrence identity, and append-only local history. If a
link request times out after the server committed it, task detail also offers
**Close linked session** for the currently active link; close it explicitly
before starting a replacement.
