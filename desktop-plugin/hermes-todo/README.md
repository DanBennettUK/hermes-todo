# Todo Desktop plugin

Choose the profile explicitly and derive its home rather than relying on the
sticky active profile:

```bash
PROFILE=default
PROFILE_HOME="$(dirname "$(hermes --profile "$PROFILE" config path)")"
```

Install this entire directory as
`$PROFILE_HOME/desktop-plugins/hermes-todo`.

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
