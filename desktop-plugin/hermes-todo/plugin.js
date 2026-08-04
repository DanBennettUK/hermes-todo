import {
  Button,
  EmptyState,
  Input,
  Tip,
  cn,
  haptic,
  host,
  icons,
  useQuery,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hermes-todo'
const LEGACY_STORAGE_KEY = 'board'
const MIGRATION_KEY = 'remote-board-v1-migrated'
const ESTIMATES = [15, 25, 45, 60]
const PLANS = new Set(['now', 'today', 'later'])
const STATUSES = new Set(['open', 'waiting', 'blocked', 'done'])
const POLL_MS = 3000

const emptyBoard = () => ({ version: 3, revision: 0, tasks: [] })

function makeId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `task-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function legacyDimensions(lane) {
  if (lane === 'now') return { plan: 'now', status: 'open' }
  if (lane === 'waiting') return { plan: 'today', status: 'waiting' }
  if (lane === 'done') return { plan: 'today', status: 'done' }
  return { plan: 'today', status: 'open' }
}

function sectionFor(task, today = localDateKey()) {
  if (task.status === 'done') return 'done'
  if (task.status === 'blocked') return 'blocked'
  if (task.status === 'waiting') return 'waiting'
  if (task.plan === 'now') return 'now'
  const due = deadlineDateKey(task)
  if (due && due <= today) return 'today'
  return task.plan
}

function normaliseTask(task) {
  if (!task || typeof task !== 'object' || typeof task.title !== 'string' || !task.title.trim()) return null
  const legacy = legacyDimensions(task.lane)
  const plan = PLANS.has(task.plan) ? task.plan : legacy.plan
  const status = STATUSES.has(task.status) ? task.status : legacy.status
  return {
    id: typeof task.id === 'string' && task.id ? task.id : makeId(),
    title: task.title.trim().slice(0, 500),
    plan,
    status,
    lane: status === 'open' ? plan : status,
    estimate: Number.isFinite(Number(task.estimate)) ? Number(task.estimate) : 25,
    dueDate: typeof task.dueDate === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(task.dueDate) ? task.dueDate : null,
    dueAt: typeof task.dueAt === 'string' && task.dueAt ? task.dueAt : null,
    dueTimezone: typeof task.dueTimezone === 'string' && task.dueTimezone ? task.dueTimezone : null,
    project: typeof task.project === 'string' && task.project ? task.project : null,
    priority: Number.isInteger(task.priority) ? task.priority : null,
    recurrence: typeof task.recurrence === 'string' && task.recurrence ? task.recurrence : null,
    source: typeof task.source === 'string' && task.source ? task.source : null,
    externalId: typeof task.externalId === 'string' && task.externalId ? task.externalId : null,
    createdAt: task.createdAt ?? new Date().toISOString(),
    updatedAt: task.updatedAt ?? task.createdAt ?? new Date().toISOString(),
    completedAt: task.completedAt ?? null
  }
}

function normaliseBoard(value) {
  if (!value || typeof value !== 'object' || !Array.isArray(value.tasks)) return emptyBoard()
  const tasks = value.tasks.map(normaliseTask).filter(Boolean)
  let keptNow = false
  for (const task of tasks) {
    if (task.status !== 'open' || task.plan !== 'now') continue
    if (keptNow) {
      task.plan = 'today'
      task.lane = 'today'
    }
    keptNow = true
  }
  return {
    version: Number(value.version) || 3,
    revision: Number(value.revision) || 0,
    tasks
  }
}

async function sharedBoardRest(ctx, path, options = {}) {
  return ctx.rest(path, options)
}

function errorText(error) {
  return error instanceof Error ? error.message : String(error || 'unknown error')
}

async function loadSharedBoard(ctx) {
  return sharedBoardRest(ctx, '/board', { timeoutMs: 5000 })
}

function timeValue(value) {
  const parsed = Date.parse(String(value || ''))
  return Number.isFinite(parsed) ? parsed : 0
}

function localDateKey(value = new Date()) {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function deadlineDateKey(task) {
  if (task.dueDate) return task.dueDate
  if (!task.dueAt) return null
  const due = new Date(task.dueAt)
  return Number.isNaN(due.getTime()) ? null : localDateKey(due)
}

function workPrompt(task) {
  const context = [
    `Hermes Todo task ID: ${task.id}`,
    `Task: ${task.title}`,
    task.project ? `Project: ${task.project}` : null,
    `Plan: ${task.plan}`,
    `Status: ${task.status}`,
    `Working estimate: ${task.estimate} minutes`,
    task.priority ? `Priority: P${task.priority}` : null,
    task.dueDate ? `Due date: ${task.dueDate}` : null,
    task.dueAt ? `Due time: ${task.dueAt}` : null,
    task.dueTimezone ? `Due timezone: ${task.dueTimezone}` : null,
    task.recurrence ? `Recurrence: ${task.recurrence}` : null,
    task.source ? `Origin: ${task.source}${task.externalId ? ` (${task.externalId})` : ''}` : null
  ].filter(Boolean)

  return [
    'Start a dedicated work session for this Hermes Todo task.',
    ...context,
    '',
    'Treat the Hermes Todo task as the authoritative work item. Help me make progress now: identify the smallest useful next action, then do safe work directly where you can. Keep the task updated when its status or plan genuinely changes.'
  ].join('\n')
}

function localDateTimeValue(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function optimisticPatch(board, id, changes) {
  const now = new Date().toISOString()
  const target = board.tasks.find(task => task.id === id)
  if (!target) return board
  const nextTarget = { ...target, ...changes }
  if (Object.prototype.hasOwnProperty.call(changes, 'dueDate') && changes.dueDate) nextTarget.dueAt = null
  if (Object.prototype.hasOwnProperty.call(changes, 'dueAt') && changes.dueAt) nextTarget.dueDate = null
  nextTarget.completedAt = nextTarget.status === 'done' ? target.completedAt || now : null
  nextTarget.updatedAt = now
  nextTarget.lane = nextTarget.status === 'open' ? nextTarget.plan : nextTarget.status

  return {
    ...board,
    tasks: board.tasks.map(task => {
      if (task.id === id) return nextTarget
      if (nextTarget.status === 'open' && nextTarget.plan === 'now' && task.status === 'open' && task.plan === 'now') {
        return { ...task, plan: 'today', lane: 'today', updatedAt: now }
      }
      return task
    })
  }
}

function useRemoteBoard(ctx) {
  const legacyBoard = useMemo(() => normaliseBoard(ctx.storage.get(LEGACY_STORAGE_KEY, emptyBoard())), [ctx.storage])
  const activeProfile = useValue(host.state.profile)
  const queryClient = useQueryClient()
  const queryKey = useMemo(() => [ID, 'board', activeProfile || 'default'], [activeProfile])
  const [pendingIds, setPendingIds] = useState(() => new Set())
  const [adding, setAdding] = useState(false)
  const migrationStarted = useRef(false)
  const writeQueue = useRef(Promise.resolve())

  const query = useQuery({
    queryKey,
    queryFn: async () => normaliseBoard(await loadSharedBoard(ctx)),
    refetchInterval: POLL_MS,
    retry: 1
  })
  const board = useMemo(() => normaliseBoard(query.data ?? legacyBoard), [legacyBoard, query.data])
  const boardRef = useRef(board)

  useEffect(() => {
    boardRef.current = board
  }, [board])

  const commitRemote = useCallback(candidate => {
    const normalised = normaliseBoard(candidate)
    if (normalised.revision >= boardRef.current.revision) {
      boardRef.current = normalised
      queryClient.setQueryData(queryKey, normalised)
    }
    return normalised
  }, [queryClient, queryKey])

  useEffect(() => {
    if (!query.isSuccess || migrationStarted.current) return
    migrationStarted.current = true
    const migrate = async () => {
      try {
        const migrated = ctx.storage.get(MIGRATION_KEY, false)
        if (!migrated && legacyBoard.tasks.length) {
          const remote = await sharedBoardRest(ctx, '/import', {
            method: 'POST',
            body: { tasks: legacyBoard.tasks },
            timeoutMs: 8000
          })
          commitRemote(remote)
        }
        if (!migrated) ctx.storage.set(MIGRATION_KEY, true)
      } catch (error) {
        migrationStarted.current = false
        host.notifyError(error, 'Could not migrate the previous Todo board')
      }
    }
    void migrate()
  }, [commitRemote, ctx, legacyBoard, query.isSuccess])

  const enqueue = useCallback(operation => {
    const run = writeQueue.current.catch(() => undefined).then(operation)
    writeQueue.current = run
    return run
  }, [])

  const update = useCallback(
    (id, changes) => enqueue(async () => {
      await queryClient.cancelQueries({ queryKey })
      const snapshot = boardRef.current
      const optimistic = optimisticPatch(snapshot, id, changes)
      boardRef.current = optimistic
      queryClient.setQueryData(queryKey, optimistic)
      setPendingIds(current => new Set(current).add(id))
      try {
        const remote = await sharedBoardRest(ctx, `/tasks/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          body: changes,
          timeoutMs: 8000
        })
        commitRemote(remote)
        return true
      } catch (error) {
        if (boardRef.current === optimistic) {
          boardRef.current = snapshot
          queryClient.setQueryData(queryKey, snapshot)
        }
        host.notifyError(error, 'Could not update the shared Todo board')
        return false
      } finally {
        setPendingIds(current => {
          const next = new Set(current)
          next.delete(id)
          return next
        })
        void queryClient.invalidateQueries({ queryKey })
      }
    }),
    [commitRemote, ctx, enqueue, queryClient, queryKey]
  )

  const cycleEstimate = useCallback(
    task => {
      const index = ESTIMATES.indexOf(task.estimate)
      const estimate = ESTIMATES[(index + 1) % ESTIMATES.length]
      return update(task.id, { estimate })
    },
    [update]
  )

  const add = useCallback(
    (title, plan = 'today') => enqueue(async () => {
      setAdding(true)
      try {
        const remote = await sharedBoardRest(ctx, '/tasks', {
          method: 'POST',
          body: { title, estimate: 25, plan, status: 'open' },
          timeoutMs: 8000
        })
        commitRemote(remote)
        return true
      } catch (error) {
        host.notifyError(error, 'Could not add the task to the shared Todo board')
        return false
      } finally {
        setAdding(false)
        void queryClient.invalidateQueries({ queryKey })
      }
    }),
    [commitRemote, ctx, enqueue, queryClient, queryKey]
  )

  const remove = useCallback(
    id => enqueue(async () => {
      setPendingIds(current => new Set(current).add(id))
      try {
        const remote = await sharedBoardRest(ctx, `/tasks/${encodeURIComponent(id)}`, {
          method: 'DELETE',
          timeoutMs: 8000
        })
        commitRemote(remote)
        return true
      } catch (error) {
        host.notifyError(error, 'Could not delete the task from the shared Todo board')
        return false
      } finally {
        setPendingIds(current => {
          const next = new Set(current)
          next.delete(id)
          return next
        })
        void queryClient.invalidateQueries({ queryKey })
      }
    }),
    [commitRemote, ctx, enqueue, queryClient, queryKey]
  )

  return {
    add,
    adding,
    board,
    connection: query.isError ? 'offline' : query.data ? 'online' : 'connecting',
    cycleEstimate,
    error: query.error ? errorText(query.error) : '',
    pendingIds,
    refresh: query.refetch,
    remove,
    update
  }
}

function IconButton({ label, icon: Icon, onClick, disabled = false, tone = 'quiet', expanded }) {
  return jsx(Tip, {
    label,
    children: jsx(Button, {
      'aria-label': label,
      'aria-expanded': expanded,
      className: cn(tone === 'accent' && 'text-(--ui-accent)'),
      disabled,
      onClick,
      size: 'icon-xs',
      type: 'button',
      variant: 'ghost',
      children: jsx(Icon, { className: 'size-3.5' })
    })
  })
}

function EstimateButton({ disabled, minutes, onClick }) {
  return jsx(Button, {
    'aria-label': `Change estimate, currently ${minutes} minutes`,
    className: 'h-auto px-1 py-0 text-[0.6875rem] font-normal tabular-nums text-(--ui-text-quaternary)',
    disabled,
    onClick,
    size: 'micro',
    type: 'button',
    variant: 'text',
    children: `${minutes}m`
  })
}

function dueLabel(task) {
  if (task.dueDate) {
    const date = new Date(`${task.dueDate}T12:00:00`)
    const label = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' }).format(date)
    const today = localDateKey()
    const tomorrowDate = new Date()
    tomorrowDate.setDate(tomorrowDate.getDate() + 1)
    const tomorrow = `${tomorrowDate.getFullYear()}-${String(tomorrowDate.getMonth() + 1).padStart(2, '0')}-${String(tomorrowDate.getDate()).padStart(2, '0')}`
    if (task.status !== 'done' && task.dueDate < today) return `Overdue · ${label}`
    if (task.dueDate === today) return 'Due today'
    if (task.dueDate === tomorrow) return 'Due tomorrow'
    return `Due ${label}`
  }
  if (task.dueAt) {
    const date = new Date(task.dueAt)
    if (!Number.isNaN(date.getTime())) {
      const label = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(date)
      return task.status !== 'done' && date.getTime() < Date.now() ? `Overdue · ${label}` : `Due ${label}`
    }
  }
  return null
}

function ChoiceButton({ active, children, disabled, onClick }) {
  return jsx(Button, {
    className: 'h-6 px-2 text-[0.6875rem]',
    disabled,
    onClick,
    size: 'xs',
    type: 'button',
    variant: active ? 'default' : 'secondary',
    children
  })
}

function TaskDetails({ task, disabled, update, remove, close }) {
  const [titleDraft, setTitleDraft] = useState(task.title)
  const [projectDraft, setProjectDraft] = useState(task.project || '')
  const [recurrenceDraft, setRecurrenceDraft] = useState(task.recurrence || '')
  const [priorityDraft, setPriorityDraft] = useState(task.priority)
  const [dueMode, setDueMode] = useState(task.dueAt ? 'timed' : 'date')
  const [dueDraft, setDueDraft] = useState(task.dueDate || '')
  const [timedDraft, setTimedDraft] = useState(localDateTimeValue(task.dueAt))
  const [confirmDelete, setConfirmDelete] = useState(false)

  const saveDetails = async event => {
    event.preventDefault()
    const title = titleDraft.trim()
    if (!title) return
    const dueChanges = dueMode === 'timed'
      ? {
          dueAt: timedDraft ? new Date(timedDraft).toISOString() : null,
          dueDate: null,
          dueTimezone: timedDraft ? Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' : null
        }
      : { dueDate: dueDraft || null, dueAt: null, dueTimezone: null }
    const saved = await update(task.id, {
      title,
      project: projectDraft.trim() || null,
      priority: priorityDraft || null,
      recurrence: recurrenceDraft.trim() || null,
      ...dueChanges
    })
    if (saved) close()
  }

  return jsxs('div', {
    className: 'mt-2 rounded-md border border-(--ui-stroke-secondary) p-2',
    onKeyDown: event => {
      if (event.key === 'Escape') close()
    },
    children: [
      jsx('div', { className: 'mb-1 text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)', children: 'Task' }),
      jsx(Input, {
        'aria-label': 'Task title',
        className: 'h-7 text-xs',
        disabled,
        maxLength: 500,
        onChange: event => setTitleDraft(event.target.value),
        value: titleDraft
      }),
      jsx('div', { className: 'mb-1 mt-2 text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)', children: 'Plan' }),
      jsxs('div', {
        className: 'flex flex-wrap gap-1',
        children: [
          jsx(ChoiceButton, { active: task.status === 'open' && task.plan === 'now', disabled, onClick: () => void update(task.id, { plan: 'now', status: 'open' }), children: 'Now' }),
          jsx(ChoiceButton, { active: task.status === 'open' && task.plan === 'today', disabled, onClick: () => void update(task.id, { plan: 'today', status: 'open' }), children: 'Today' }),
          jsx(ChoiceButton, { active: task.status === 'open' && task.plan === 'later', disabled, onClick: () => void update(task.id, { plan: 'later', status: 'open' }), children: 'Later' })
        ]
      }),
      jsx('div', { className: 'mb-1 mt-2 text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)', children: 'Status' }),
      jsxs('div', {
        className: 'flex flex-wrap gap-1',
        children: [
          jsx(ChoiceButton, { active: task.status === 'open', disabled, onClick: () => void update(task.id, { status: 'open' }), children: 'Open' }),
          jsx(ChoiceButton, { active: task.status === 'waiting', disabled, onClick: () => void update(task.id, { status: 'waiting' }), children: 'Waiting' }),
          jsx(ChoiceButton, { active: task.status === 'blocked', disabled, onClick: () => void update(task.id, { status: 'blocked' }), children: 'Blocked' })
        ]
      }),
      jsx('div', { className: 'mb-1 mt-2 text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)', children: 'Deadline' }),
      jsxs('div', {
        className: 'mb-1 flex gap-1',
        children: [
          jsx(ChoiceButton, { active: dueMode === 'date', disabled, onClick: () => setDueMode('date'), children: 'All day' }),
          jsx(ChoiceButton, { active: dueMode === 'timed', disabled, onClick: () => setDueMode('timed'), children: 'Timed' })
        ]
      }),
      dueMode === 'timed'
        ? jsx(Input, {
            'aria-label': 'Timed deadline',
            className: 'h-7 text-xs',
            disabled,
            onChange: event => setTimedDraft(event.target.value),
            type: 'datetime-local',
            value: timedDraft
          })
        : jsx(Input, {
            'aria-label': 'Due date',
            className: 'h-7 text-xs',
            disabled,
            onChange: event => setDueDraft(event.target.value),
            type: 'date',
            value: dueDraft
          }),
      jsx('div', { className: 'mb-1 mt-2 text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)', children: 'Project' }),
      jsx(Input, {
        'aria-label': 'Project',
        className: 'h-7 text-xs',
        disabled,
        maxLength: 500,
        onChange: event => setProjectDraft(event.target.value),
        placeholder: 'Optional',
        value: projectDraft
      }),
      jsx('div', { className: 'mb-1 mt-2 text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)', children: 'Priority' }),
      jsx('div', {
        className: 'flex flex-wrap gap-1',
        children: [null, 1, 2, 3, 4].map(value => jsx(ChoiceButton, {
          active: priorityDraft === value,
          disabled,
          onClick: () => setPriorityDraft(value),
          children: value === null ? 'None' : `P${value}`
        }, String(value)))
      }),
      jsx('div', { className: 'mb-1 mt-2 text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)', children: 'Recurrence note' }),
      jsx(Input, {
        'aria-label': 'Recurrence note',
        className: 'h-7 text-xs',
        disabled,
        maxLength: 500,
        onChange: event => setRecurrenceDraft(event.target.value),
        placeholder: 'Optional',
        value: recurrenceDraft
      }),
      jsx('div', {
        className: 'mt-2 flex items-center gap-1',
        children: [
          jsx(Button, { disabled: disabled || !titleDraft.trim(), onClick: event => void saveDetails(event), size: 'xs', type: 'button', variant: 'secondary', children: 'Save details' }),
          jsx(Button, {
            disabled,
            onClick: async () => {
              if (!confirmDelete) {
                setConfirmDelete(true)
                return
              }
              if (await remove(task.id)) close()
            },
            size: 'xs',
            type: 'button',
            variant: 'text',
            children: confirmDelete ? 'Confirm delete' : 'Delete'
          }),
          jsx(Button, { disabled, onClick: close, size: 'xs', type: 'button', variant: 'text', children: 'Cancel' })
        ]
      })
    ]
  })
}

function TaskRow({ task, update, remove, cycleEstimate, pending, workingId, workWithHermes, prominent = false }) {
  const [editing, setEditing] = useState(false)
  const disabled = pending || workingId === task.id
  const due = dueLabel(task)

  return jsxs('div', {
    className: cn(
      'group w-full min-w-0 max-w-full overflow-hidden border-b border-(--ui-stroke-secondary) py-2 last:border-b-0',
      prominent && 'border-l-2 pl-2.5'
    ),
    style: prominent ? { borderLeftColor: 'var(--ui-accent)' } : undefined,
    children: [
      jsxs('div', {
        className: 'flex min-w-0 items-start gap-2',
        children: [
          jsxs('div', {
            className: 'min-w-0 flex-1',
            children: [
              jsx('div', {
                className: cn(
                  'break-words [overflow-wrap:anywhere] text-xs leading-5 text-(--ui-text-primary)',
                  task.status === 'done' && 'text-(--ui-text-quaternary) line-through'
                ),
                children: task.title
              }),
              (due || task.project || task.priority || task.recurrence) && jsx('div', {
                className: cn(
                  'mt-0.5 truncate text-[0.625rem] text-(--ui-text-quaternary)',
                  due?.startsWith('Overdue') && 'font-medium text-(--ui-text-secondary)'
                ),
                children: [due, task.project, task.priority ? `P${task.priority}` : null, task.recurrence].filter(Boolean).join(' · ')
              })
            ]
          }),
          jsxs('div', {
            className: 'flex shrink-0 items-center gap-0.5 self-start',
            children: [
              jsx(EstimateButton, { disabled, minutes: task.estimate, onClick: () => void cycleEstimate(task) }),
              jsx(IconButton, {
                disabled,
                expanded: editing,
                icon: icons.MoreHorizontal,
                label: 'Plan, status and due date',
                onClick: () => setEditing(value => !value)
              }),
              task.status === 'done'
                ? jsx(IconButton, { disabled, icon: icons.RefreshCw, label: 'Reopen', onClick: () => void update(task.id, { status: 'open' }) })
                : jsx(IconButton, { disabled, icon: icons.Check, label: 'Complete', onClick: () => {
                    haptic('success')
                    void update(task.id, { status: 'done' })
                  } })
            ]
          })
        ]
      }),
      editing && jsx(TaskDetails, { close: () => setEditing(false), disabled, remove, task, update }),
      task.status !== 'done' && jsx(Button, {
        className: cn('mt-1.5', prominent ? '' : 'opacity-80 group-hover:opacity-100'),
        disabled,
        onClick: () => void workWithHermes(task),
        size: 'xs',
        type: 'button',
        variant: prominent ? 'default' : 'secondary',
        children: jsxs('span', {
          className: 'inline-flex items-center gap-1',
          children: [
            jsx(icons.MessageCircle, { className: 'size-3' }),
            workingId === task.id ? 'Sending…' : 'Work with Hermes'
          ]
        })
      })
    ]
  })
}

function Section({ title, count, children, muted = false }) {
  return jsxs('section', {
    className: 'mt-4 min-w-0 max-w-full first:mt-0',
    children: [
      jsxs('div', {
        className: 'mb-1.5 flex items-baseline justify-between gap-2',
        children: [
          jsx('h3', { className: cn('text-xs font-semibold text-(--ui-text-secondary)', muted && 'text-(--ui-text-tertiary)'), children: title }),
          jsx('span', { className: 'text-[0.6875rem] tabular-nums text-(--ui-text-quaternary)', children: count })
        ]
      }),
      children
    ]
  })
}

function CollapsibleSection(props) {
  if (!props.tasks.length) return null
  return jsxs('details', {
    className: 'mt-4 min-w-0 max-w-full overflow-hidden',
    open: props.open || undefined,
    children: [
      jsxs('summary', {
        className: 'flex cursor-pointer list-none items-center justify-between text-xs font-semibold text-(--ui-text-tertiary)',
        children: [jsx('span', { children: props.title }), jsx('span', { className: 'font-normal tabular-nums', children: props.tasks.length })]
      }),
      jsx('div', {
        className: 'mt-1.5 min-w-0 max-w-full overflow-hidden',
        children: props.tasks.map(task => jsx(TaskRow, { ...props.rowProps, pending: props.pendingIds.has(task.id), task }, task.id))
      })
    ]
  })
}

function TodoPane({ ctx }) {
  const remote = useRemoteBoard(ctx)
  const [draft, setDraft] = useState('')
  const [filter, setFilter] = useState('')
  const [todayKey, setTodayKey] = useState(localDateKey)
  const [workingId, setWorkingId] = useState(null)
  const workPending = useRef(new Set())
  const gateway = useValue(host.state.gateway)
  const sessionId = useValue(host.state.activeSessionId)

  const sections = useMemo(() => {
    const grouped = { now: [], today: [], later: [], waiting: [], blocked: [], done: [] }
    const needle = filter.trim().toLocaleLowerCase()
    for (const task of remote.board.tasks) {
      const haystack = [task.title, task.project, task.recurrence].filter(Boolean).join(' ').toLocaleLowerCase()
      if (!needle || haystack.includes(needle)) grouped[sectionFor(task, todayKey)].push(task)
    }
    const dueThenCreated = (a, b) => {
      const aDue = a.dueDate || a.dueAt || '9999'
      const bDue = b.dueDate || b.dueAt || '9999'
      return aDue.localeCompare(bDue) || timeValue(a.createdAt) - timeValue(b.createdAt)
    }
    for (const key of ['now', 'today', 'later', 'waiting', 'blocked']) grouped[key].sort(dueThenCreated)
    grouped.done.sort((a, b) => timeValue(b.completedAt) - timeValue(a.completedAt))
    return grouped
  }, [filter, remote.board, todayKey])

  useEffect(() => {
    const timer = setInterval(() => setTodayKey(localDateKey()), 60_000)
    return () => clearInterval(timer)
  }, [])

  const openCount = remote.board.tasks.filter(task => task.status !== 'done').length
  const shownCount = Object.values(sections).reduce((count, tasks) => count + tasks.length, 0)
  const dateLabel = useMemo(
    () => new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short' }).format(new Date()),
    []
  )

  const addTask = async event => {
    event.preventDefault()
    const title = draft.trim()
    if (!title || remote.adding) return
    const hasOpenNow = remote.board.tasks.some(task => task.status === 'open' && task.plan === 'now')
    const added = await remote.add(title.slice(0, 500), hasOpenNow ? 'today' : 'now')
    if (added) {
      setDraft('')
      haptic('success')
    }
  }

  const workWithHermes = useCallback(
    async task => {
      if (workPending.current.has(task.id)) return
      if (host.state.gateway.get() !== 'open') {
        host.notify({ kind: 'warning', message: 'Connect Hermes before starting this task.' })
        return
      }
      workPending.current.add(task.id)
      setWorkingId(task.id)
      let createdSession = null
      try {
        const params = {
          cols: 96,
          source: 'desktop',
          title: task.title.slice(0, 160)
        }
        const cwd = host.state.cwd.get().trim()
        const profile = host.state.profile.get().trim()
        const model = host.state.model.get().trim()
        if (cwd) params.cwd = cwd
        if (profile) params.profile = profile
        if (model) params.model = model

        createdSession = await host.request('session.create', params)
        if (!createdSession?.session_id || !createdSession?.stored_session_id) {
          throw new Error('Hermes did not return a usable new session')
        }
        const focused = await remote.update(task.id, { plan: 'now' })
        if (!focused) {
          await host.request('session.close', { session_id: createdSession.session_id }).catch(() => undefined)
          return
        }
        await host.request('prompt.submit', { session_id: createdSession.session_id, text: workPrompt({ ...task, plan: 'now' }) })
        host.navigate(`/${encodeURIComponent(createdSession.stored_session_id)}`)
      } catch (error) {
        if (createdSession?.session_id) {
          await host.request('session.close', { session_id: createdSession.session_id }).catch(() => undefined)
        }
        host.notifyError(error, 'Could not send this task to Hermes')
      } finally {
        workPending.current.delete(task.id)
        setWorkingId(null)
      }
    },
    [remote]
  )

  const connectionLabel = remote.connection === 'online'
    ? 'v0.1.0 · Shared with Hermes'
    : remote.connection === 'connecting'
      ? 'v0.1.0 · Connecting…'
      : `v0.1.0 · Offline: ${remote.error || 'request failed'}`

  const rowProps = {
    cycleEstimate: remote.cycleEstimate,
    pending: false,
    remove: remote.remove,
    update: remote.update,
    workingId,
    workWithHermes
  }

  return jsxs('div', {
    className: 'flex h-full min-h-0 flex-col text-sm',
    children: [
      jsxs('header', {
        className: 'border-b border-(--ui-stroke-secondary) px-3 py-3',
        children: [
          jsxs('div', {
            className: 'flex items-start justify-between gap-3',
            children: [
              jsxs('div', {
                children: [
                  jsx('h2', { className: 'text-sm font-semibold text-(--ui-text-primary)', children: 'Todo' }),
                  jsx('p', { className: 'mt-0.5 text-[0.6875rem] text-(--ui-text-quaternary)', children: dateLabel })
                ]
              }),
              jsxs('div', {
                className: 'text-right',
                children: [
                  jsx('div', { className: 'text-xs font-medium tabular-nums text-(--ui-text-secondary)', children: openCount }),
                  jsx('div', { className: 'text-[0.625rem] text-(--ui-text-quaternary)', children: 'open' })
                ]
              })
            ]
          }),
          jsxs('form', {
            className: 'mt-3 flex gap-1.5',
            onSubmit: event => void addTask(event),
            children: [
              jsx(Input, {
                'aria-label': 'Add a task',
                className: 'min-w-0 flex-1',
                disabled: remote.connection === 'offline',
                maxLength: 500,
                onChange: event => setDraft(event.target.value),
                placeholder: 'Add something real',
                value: draft
              }),
              jsx(Button, {
                'aria-label': 'Add task',
                disabled: !draft.trim() || remote.adding || remote.connection === 'offline',
                size: 'icon-sm',
                type: 'submit',
                children: jsx(icons.Plus, { className: 'size-3.5' })
              })
            ]
          }),
          remote.board.tasks.length > 12 && jsxs('div', {
            className: 'mt-2 flex items-center gap-2',
            children: [
              jsx(Input, {
                'aria-label': 'Filter tasks',
                className: 'h-7 min-w-0 flex-1 text-xs',
                onChange: event => setFilter(event.target.value),
                placeholder: 'Filter tasks or projects',
                value: filter
              }),
              jsx('span', {
                className: 'shrink-0 text-[0.625rem] tabular-nums text-(--ui-text-quaternary)',
                children: filter ? `${shownCount} shown` : `${remote.board.tasks.length} total`
              })
            ]
          })
        ]
      }),
      jsx('div', {
        className: 'min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto',
        children: jsxs('div', {
          className: 'w-full min-w-0 max-w-full px-3 py-3',
          children: [
            jsx(Section, {
              count: sections.now.length,
              title: 'Now',
              children: sections.now.length
                ? sections.now.map(task => jsx(TaskRow, { ...rowProps, pending: remote.pendingIds.has(task.id), prominent: true, task }, task.id))
                : jsx('div', {
                    className: 'border-l-2 border-l-(--ui-stroke-secondary) py-2 pl-2.5 text-xs leading-5 text-(--ui-text-quaternary)',
                    children: sections.today.length ? 'Pick one thing from Today.' : 'Add one thing. It becomes Now.'
                  })
            }),
            jsx(Section, {
              count: sections.today.length,
              title: 'Today',
              children: sections.today.length
                ? sections.today.map(task => jsx(TaskRow, { ...rowProps, pending: remote.pendingIds.has(task.id), task }, task.id))
                : jsx(EmptyState, {
                    className: 'min-h-20 py-3',
                    description: 'Nothing else is competing for attention.',
                    title: 'Clear'
                  })
            }),
            jsx(CollapsibleSection, { pendingIds: remote.pendingIds, rowProps, tasks: sections.later, title: 'Later' }),
            jsx(CollapsibleSection, { open: true, pendingIds: remote.pendingIds, rowProps, tasks: sections.blocked, title: 'Blocked' }),
            jsx(CollapsibleSection, { pendingIds: remote.pendingIds, rowProps, tasks: sections.waiting, title: 'Waiting' }),
            jsx(CollapsibleSection, { pendingIds: remote.pendingIds, rowProps, tasks: sections.done, title: 'Closed' })
          ]
        })
      }),
      jsxs('footer', {
        className: 'flex items-center justify-between gap-2 border-t border-(--ui-stroke-secondary) px-3 py-1.5 text-[0.625rem] text-(--ui-text-quaternary)',
        children: [
          jsx(Button, {
            className: 'h-auto p-0 text-[0.625rem] font-normal',
            onClick: () => void remote.refresh(),
            size: 'micro',
            title: connectionLabel,
            type: 'button',
            variant: 'text',
            children: connectionLabel
          }),
          jsx('span', {
            className: cn(gateway === 'open' && sessionId ? 'text-(--ui-text-tertiary)' : 'text-(--ui-text-quaternary)'),
            children: gateway === 'open' && sessionId ? 'Hermes ready' : 'Open a conversation'
          })
        ]
      })
    ]
  })
}

export default {
  id: ID,
  name: 'Todo',
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'todo',
      data: {
        placement: 'right',
        dock: { pane: 'workspace', pos: 'right' },
        width: '360px'
      },
      render: () => jsx(TodoPane, { ctx })
    })
  }
}
