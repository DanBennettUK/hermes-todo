---
name: hermes-todo-task
description: Use when working a Hermes Todo task. Board stays truthful.
version: 1.0.0
tags:
  - tasks
  - todo
  - project-management
---

# Working a Hermes Todo Task

You are working inside a session launched for ONE Hermes Todo task. The board is the shared
source of truth between the user's desktop Todo pane and every agent. Do the work, and keep
the task record truthful while you do it. The board is not paperwork; it is how the user sees
progress without asking.

## The tool

Use the `todo_board` tool. Read with `list` / `get`; write with `update`, `reorder`, `start`,
`done`, `create`. Every task has independent dimensions: `plan` (`now` / `today` / `later`, where
`now` is the single running item), `status` (open / waiting / blocked / done), `category`
(`today`, `tomorrow`, `this-week`, `this-month`, `soon`), and `position` (sort order inside
a category).
Free-text fields: `brief`, `next_action`, `closure_condition`, `waiting_on`, `blocker`,
`review_date`, `owner`, `project`, `due_date`/`due_at`, `priority` (1-4). Artefact and
closure-evidence lists ride on updates and the `done` action.

## Update discipline (the core habit)

Update the task AS you work, not after. The user watches the pane in real time.

1. **On taking the task**: confirm `next_action` states the smallest live move. If the
   `closure_condition` is empty or vague, write one: a checkable sentence for what proves
   completion. If `brief` is missing context you were given, add it. Set `owner` if unowned.
2. **On progress**: keep `next_action` pointing at the current smallest move. Replace it as
   you go, never append a diary. If you finish a meaningful part, update `brief` with one
   short line of durable state (decisions made, where things stand).
3. **On partial completion**: if a distinct slice is done, say so in `brief`
   ("Done: X. Remaining: Y.") and reflect progress percentage in the same line. Keep
   `status` open until the WHOLE closure condition holds. Never set done for partial work.
4. **On blockers**: the moment progress stalls, set `status: blocked` with `blocker` naming
   the concrete obstacle, and `next_action` pointing at the unblocking move. When unblocked,
   clear `blocker` and return `status: open`. Same pattern for waiting on a person:
   `status: waiting` with `waiting_on` naming who, `review_date` for the follow-up.
5. **On difficulty discovery**: if the task turns out bigger or riskier than the `estimate`
   assumed, update `estimate` (minutes, 5-480) and say why in one `brief` line.
6. **On artefacts**: every durable thing you produce (file path, URL, run log location)
   belongs in the task. Update `artefacts` (list of strings, max 20, one item per path/link)
   as you create them. Prefer absolute paths.
7. **On labels and placement**: move the task between categories when its real horizon
   changes. Use `reorder` with `before_id`/`after_id` when order matters. Use the `start`
   action to make it the single Now item; never hand-set `plan: now` on a second task.
8. **On completion**: done means the `closure_condition` holds AND the user confirms (or the
   evidence is machine-checkable and passes). Then use the `done` action with `closure_note`
   (what was delivered, 1-2 sentences) and `evidence` (paths/commands that prove it). Truth
   over optics: "drafted locally" is not "shipped". Never mark done on inference that the
   user will be happy; ask if unsure.
9. **On completion inside a goal loop**: finishing the task should coincide with the goal
   completing. Mark done only when both hold.

## Goal gating (use /goal for sustained work)

When the task needs multiple autonomous turns, start a goal so completion is gated:

```
/goal <one-line objective>
outcome: <restated closure condition in done-when form>
verification: <how it will be checked: command, path, or user confirmation>
constraints: <budget, scope limits, don't-touch list>
```

- Always copy the task's `closure_condition` into the goal's `outcome`, rephrased as
  "done when ...". The goal engine judges completion against this contract, so a vague
  outcome means the loop never ends or ends early.
- `verification` names concrete evidence: the test command, the file to check, or
  "user confirms in chat".
- Keep `constraints` short and real (time box, no new deps, stay in scope).
- If the goal engine is unavailable, fall back to manual discipline: work in small turns,
  update the task each turn, and ask before marking done.

## Reading the task like a project manager

- `brief` is accumulated durable context. Read it before planning; it carries decisions
  already made so you do not relitigate them.
- `next_action` is the handoff between your turns. If you resume a task and `next_action`
  is stale relative to `brief`, fix it first.
- `waiting_on` / `blocker` tell you why a task is parked. Do not bulldoze a waiting task
  without noting why the wait ended.
- `artefacts` is where prior sessions left their outputs. Check there before redoing work.
- `priority` P1-P3 (pill on the card) outranks category order when picking the next task;
  P4/none is normal priority.
- Never delete tasks: the tool has no delete, by design. If a task is obsolete, close it
  with `done` and an honest `closure_note` ("obsolete, superseded by <id>") or leave it
  parked and tell the user.

## Pitfalls

- `todo_board` update with `waiting_on: ""` clears; omitting the field leaves it unchanged.
  Pass explicit empties only when you mean to clear.
- `due_date` (YYYY-MM-DD) and `due_at` (ISO datetime) are mutually exclusive; setting one
  clears the other.
- The board is shared: another writer (the user dragging cards, another agent) can change it
  mid-session. Re-read with `get` before a precise `update` if the change matters.
- Expected-revision conflicts (error `revision_conflict`) mean someone wrote first. Re-read,
  reapply your change on the fresh state, never retry blind.
- Estimates are whole minutes, 5-480.
