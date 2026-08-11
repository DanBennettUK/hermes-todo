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

const emptyBoard = () => ({ version: 4, revision: 0, tasks: [] })

// Hermes Desktop evaluates disk plugins as one uncompiled ESM module loaded
// from a blob URL. Keep this formatter inline: relative imports such as
// './work-prompt.mjs' cannot resolve from that non-hierarchical base URL.
function buildWorkPrompt(task) {
  // Keep this an explicit re-entry allowlist: private sourcePayload and historical closure metadata stay in Todo storage.
  const conciseTitle = typeof task.title === 'string' ? task.title.trim().slice(0, 160) : ''
  const context = [
    `Hermes Todo task ID: ${task.id}`,
    `Task: ${conciseTitle}`,
    task.project ? `Project: ${task.project}` : null,
    `Plan: ${task.plan}`,
    `Status: ${task.status}`,
    `Working estimate: ${task.estimate} minutes`,
    task.priority ? `Priority: P${task.priority}` : null,
    task.dueDate ? `Due date: ${task.dueDate}` : null,
    task.dueAt ? `Due time: ${task.dueAt}` : null,
    task.dueTimezone ? `Due timezone: ${task.dueTimezone}` : null,
    task.recurrence ? `Recurrence: ${task.recurrence}` : null,
    task.recurrenceRule ? `Executable recurrence: ${task.recurrenceRule} (${task.recurrenceTimezone || 'UTC'})` : null,
    task.brief ? `Brief and prior decisions:\n${task.brief}` : null,
    task.nextAction ? `Next action: ${task.nextAction}` : null,
    task.closureCondition ? `Closure condition: ${task.closureCondition}` : null,
    task.waitingOn ? `Waiting on: ${task.waitingOn}` : null,
    task.blocker ? `Blocker: ${task.blocker}` : null,
    task.owner ? `Owner: ${task.owner}` : null,
    `Execution mode: ${task.executionMode || 'manual'}`,
    `Approval state: ${task.approvalState || 'not-required'}`,
    task.artefacts?.length ? `Artefacts:\n${task.artefacts.map(value => `- ${value}`).join('\n')}` : null,
    task.source ? `Origin: ${task.source}${task.externalId ? ` (${task.externalId})` : ''}` : null
  ].filter(Boolean)

  return [
    'Start a dedicated work session for this Hermes Todo task.',
    ...context,
    '',
    'Treat the Hermes Todo task as the authoritative work item. Help me make progress now: identify the smallest useful next action, then do safe work directly where you can. Keep the task updated when its status or plan genuinely changes. Record closure evidence truthfully; never describe an external delivery as verified when it is only drafted or locally checked.'
  ].join('\n')
}

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
  if (task.inbox) return 'inbox'
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
    ...task,
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
    brief: typeof task.brief === 'string' && task.brief ? task.brief : null,
    nextAction: typeof task.nextAction === 'string' && task.nextAction ? task.nextAction : null,
    closureCondition: typeof task.closureCondition === 'string' && task.closureCondition ? task.closureCondition : null,
    waitingOn: typeof task.waitingOn === 'string' && task.waitingOn ? task.waitingOn : null,
    reviewDate: typeof task.reviewDate === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(task.reviewDate) ? task.reviewDate : null,
    blocker: typeof task.blocker === 'string' && task.blocker ? task.blocker : null,
    artefacts: Array.isArray(task.artefacts) ? task.artefacts.filter(value => typeof value === 'string').slice(0, 20) : [],
    owner: typeof task.owner === 'string' && task.owner ? task.owner : null,
    executionMode: ['manual', 'supervised', 'autonomous'].includes(task.executionMode) ? task.executionMode : 'manual',
    approvalState: ['not-required', 'pending', 'approved', 'rejected'].includes(task.approvalState) ? task.approvalState : 'not-required',
    inbox: task.inbox === true,
    closureNote: typeof task.closureNote === 'string' && task.closureNote ? task.closureNote : null,
    closureEvidence: Array.isArray(task.closureEvidence) ? task.closureEvidence.filter(value => typeof value === 'string').slice(0, 20) : [],
    recurrenceRule: typeof task.recurrenceRule === 'string' && task.recurrenceRule ? task.recurrenceRule : null,
    recurrenceTimezone: typeof task.recurrenceTimezone === 'string' && task.recurrenceTimezone ? task.recurrenceTimezone : null,
    seriesId: typeof task.seriesId === 'string' && task.seriesId ? task.seriesId : null,
    occurrenceId: typeof task.occurrenceId === 'string' && task.occurrenceId ? task.occurrenceId : null,
    occurrenceNumber: Number.isInteger(task.occurrenceNumber) ? task.occurrenceNumber : null,
    sessionId: typeof task.sessionId === 'string' && task.sessionId ? task.sessionId : null,
    sessionState: ['active', 'completed'].includes(task.sessionState) ? task.sessionState : null,
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
    version: Number(value.version) || 4,
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

function safeTimeZone(value) {
  if (typeof value !== 'string' || !value) return undefined
  try {
    new Intl.DateTimeFormat(undefined, { timeZone: value }).format(new Date(0))
    return value
  } catch {
    return undefined
  }
}

function dateKeyInTimeZone(value, timeZone) {
  if (!timeZone) return localDateKey(value)
  const parts = new Intl.DateTimeFormat('en', {
    day: '2-digit',
    month: '2-digit',
    timeZone,
    year: 'numeric'
  }).formatToParts(value)
  const byType = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${byType.year}-${byType.month}-${byType.day}`
}

function zonedDateTimeToDate(value, timeZone) {
  const match = String(value || '').match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/
  )
  if (!match || !timeZone) return new Date(value)
  const wallTime = Date.UTC(
    Number(match[1]), Number(match[2]) - 1, Number(match[3]),
    Number(match[4]), Number(match[5]), Number(match[6] || 0)
  )
  if (!Number.isFinite(wallTime)) return new Date(value)
  let candidate = new Date(wallTime)
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const parts = new Intl.DateTimeFormat('en', {
      day: '2-digit',
      hour: '2-digit',
      hourCycle: 'h23',
      minute: '2-digit',
      month: '2-digit',
      second: '2-digit',
      timeZone,
      year: 'numeric'
    }).formatToParts(candidate)
    const byType = Object.fromEntries(parts.map(part => [part.type, part.value]))
    const localTime = Date.UTC(
      Number(byType.year), Number(byType.month) - 1, Number(byType.day),
      Number(byType.hour), Number(byType.minute), Number(byType.second)
    )
    const next = new Date(wallTime - (localTime - candidate.getTime()))
    if (next.getTime() === candidate.getTime()) return candidate
    candidate = next
  }
  return candidate
}

function deadlineDateKey(task) {
  if (task.dueDate) return task.dueDate
  if (!task.dueAt) return null
  const dueText = String(task.dueAt)
  const taskTimeZone = safeTimeZone(task.dueTimezone)
  const naiveInTaskZone = !/(?:Z|[+-]\d{2}:\d{2})$/i.test(dueText) && Boolean(taskTimeZone)
  const due = naiveInTaskZone ? zonedDateTimeToDate(dueText, taskTimeZone) : new Date(dueText)
  if (Number.isNaN(due.getTime())) return null
  return localDateKey(due)
}

function localDateTimeValue(value, timeZone) {
  if (!value) return ''
  const text = String(value)
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(text) && safeTimeZone(timeZone)) {
    return text.slice(0, 16)
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const cleanTimeZone = safeTimeZone(timeZone)
  if (cleanTimeZone) {
    const parts = new Intl.DateTimeFormat('en', {
      day: '2-digit',
      hour: '2-digit',
      hourCycle: 'h23',
      minute: '2-digit',
      month: '2-digit',
      timeZone: cleanTimeZone,
      year: 'numeric'
    }).formatToParts(date)
    const byType = Object.fromEntries(parts.map(part => [part.type, part.value]))
    return `${byType.year}-${byType.month}-${byType.day}T${byType.hour}:${byType.minute}`
  }
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

  const commitMutation = useCallback(candidate => {
    if (Array.isArray(candidate?.tasks) && !candidate.task && !candidate.deletedId) {
      return commitRemote(candidate)
    }
    const current = boardRef.current
    if (!candidate || Number(candidate.revision) < current.revision) return current
    const byId = new Map(current.tasks.map(task => [task.id, task]))
    if (candidate.deletedId) byId.delete(candidate.deletedId)
    for (const task of [candidate.task, ...(candidate.affectedTasks || []), candidate.generatedTask, candidate.followUpTask]) {
      const normalised = normaliseTask(task)
      if (normalised) byId.set(normalised.id, normalised)
    }
    const next = normaliseBoard({
      version: candidate.version || current.version,
      revision: candidate.revision,
      tasks: [...byId.values()]
    })
    boardRef.current = next
    queryClient.setQueryData(queryKey, next)
    return next
  }, [commitRemote, queryClient, queryKey])

  const invalidateRelated = useCallback(id => {
    void queryClient.invalidateQueries({ queryKey })
    if (id) void queryClient.invalidateQueries({ queryKey: [ID, 'history', activeProfile || 'default', id] })
  }, [activeProfile, queryClient, queryKey])

  useEffect(() => {
    if (!query.isSuccess || migrationStarted.current) return
    migrationStarted.current = true
    const migrate = async () => {
      try {
        const migrated = ctx.storage.get(MIGRATION_KEY, false)
        if (!migrated && legacyBoard.tasks.length) {
          const remote = await sharedBoardRest(ctx, '/import?envelope=board', {
            method: 'POST',
            body: { tasks: legacyBoard.tasks, eventSource: 'desktop' },
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
        const remote = await sharedBoardRest(ctx, `/tasks/${encodeURIComponent(id)}?envelope=result`, {
          method: 'PATCH',
          body: { ...changes, expectedRevision: snapshot.revision, eventSource: 'desktop' },
          timeoutMs: 8000
        })
        commitMutation(remote)
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
        invalidateRelated(id)
      }
    }),
    [commitMutation, ctx, enqueue, invalidateRelated, queryClient, queryKey]
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
    title => enqueue(async () => {
      setAdding(true)
      try {
        const snapshot = boardRef.current
        const remote = await sharedBoardRest(ctx, '/tasks?envelope=result', {
          method: 'POST',
          body: {
            title,
            estimate: 25,
            plan: 'later',
            status: 'open',
            inbox: true,
            expectedRevision: snapshot.revision,
            eventSource: 'desktop'
          },
          timeoutMs: 8000
        })
        commitMutation(remote)
        return true
      } catch (error) {
        host.notifyError(error, 'Could not add the task to the shared Todo board')
        return false
      } finally {
        setAdding(false)
        invalidateRelated()
      }
    }),
    [commitMutation, ctx, enqueue, invalidateRelated]
  )

  const remove = useCallback(
    id => enqueue(async () => {
      setPendingIds(current => new Set(current).add(id))
      try {
        const snapshot = boardRef.current
        const remote = await sharedBoardRest(ctx, `/tasks/${encodeURIComponent(id)}?expectedRevision=${snapshot.revision}&envelope=result`, {
          method: 'DELETE',
          timeoutMs: 8000
        })
        commitMutation(remote)
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
        invalidateRelated(id)
      }
    }),
    [commitMutation, ctx, enqueue, invalidateRelated]
  )

  const linkSession = useCallback(
    (id, sessionId) => enqueue(async () => {
      setPendingIds(current => new Set(current).add(id))
      try {
        const snapshot = boardRef.current
        const remote = await sharedBoardRest(ctx, `/tasks/${encodeURIComponent(id)}/session?envelope=result`, {
          method: 'POST',
          body: {
            sessionId,
            startNow: true,
            expectedRevision: snapshot.revision,
            eventSource: 'desktop'
          },
          timeoutMs: 8000
        })
        commitMutation(remote)
        return true
      } catch (error) {
        host.notifyError(error, 'Could not link the Hermes work session')
        return false
      } finally {
        setPendingIds(current => {
          const next = new Set(current)
          next.delete(id)
          return next
        })
        invalidateRelated(id)
      }
    }),
    [commitMutation, ctx, enqueue, invalidateRelated]
  )

  const completeSession = useCallback(
    id => enqueue(async () => {
      setPendingIds(current => new Set(current).add(id))
      try {
        const snapshot = boardRef.current
        const remote = await sharedBoardRest(ctx, `/tasks/${encodeURIComponent(id)}/session/complete?envelope=result`, {
          method: 'POST',
          body: { expectedRevision: snapshot.revision, eventSource: 'desktop' },
          timeoutMs: 8000
        })
        commitMutation(remote)
        return true
      } catch (error) {
        host.notifyError(error, 'Could not close the linked Todo work lifecycle')
        return false
      } finally {
        setPendingIds(current => {
          const next = new Set(current)
          next.delete(id)
          return next
        })
        invalidateRelated(id)
      }
    }),
    [commitMutation, ctx, enqueue, invalidateRelated]
  )

  return {
    add,
    adding,
    board,
    completeSession,
    connection: query.isError ? 'offline' : query.data ? 'online' : 'connecting',
    cycleEstimate,
    error: query.error ? errorText(query.error) : '',
    linkSession,
    pendingIds,
    refresh: () => query.refetch(),
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
    const dueText = String(task.dueAt)
    const taskTimeZone = safeTimeZone(task.dueTimezone)
    const naiveInTaskZone = !/(?:Z|[+-]\d{2}:\d{2})$/i.test(dueText) && Boolean(taskTimeZone)
    const date = naiveInTaskZone ? zonedDateTimeToDate(dueText, taskTimeZone) : new Date(dueText)
    if (!Number.isNaN(date.getTime())) {
      const label = new Intl.DateTimeFormat(undefined, {
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        month: 'short',
        timeZone: taskTimeZone
      }).format(date)
      const overdue = date.getTime() < Date.now()
      return task.status !== 'done' && overdue ? `Overdue · ${label}` : `Due ${label}`
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

function FieldLabel({ children }) {
  return jsx('div', {
    className: 'mb-1 mt-2 text-[0.6875rem] font-medium text-(--ui-text-tertiary)',
    children
  })
}

function TextAreaField({ label, value, onChange, disabled, maxLength, placeholder }) {
  return jsxs('label', {
    className: 'block',
    children: [
      jsx(FieldLabel, { children: label }),
      jsx('textarea', {
        className: 'min-h-16 w-full resize-y rounded-md border border-(--ui-stroke-secondary) bg-transparent px-2 py-1.5 text-xs leading-5 text-(--ui-text-primary) outline-none focus-visible:ring-1 focus-visible:ring-(--ui-accent)',
        disabled,
        maxLength,
        name: label.toLocaleLowerCase().replaceAll(' ', '-'),
        onChange,
        placeholder,
        value
      })
    ]
  })
}

function lines(value) {
  return value.split('\n').map(item => item.trim()).filter(Boolean).slice(0, 20)
}

function makeTaskDetailsDraft(task, localTimeZone) {
  return {
    approvalState: task.approvalState || 'not-required',
    artefacts: [...(task.artefacts || [])],
    blocker: task.blocker || '',
    brief: task.brief || '',
    closureCondition: task.closureCondition || '',
    closureEvidence: [...(task.closureEvidence || [])],
    closureNote: task.closureNote || '',
    dueAt: localDateTimeValue(task.dueAt, task.dueTimezone),
    dueDate: task.dueDate || '',
    dueMode: task.dueAt ? 'timed' : 'date',
    dueTimezone: safeTimeZone(task.dueTimezone) || localTimeZone,
    executionMode: task.executionMode || 'manual',
    nextAction: task.nextAction || '',
    owner: task.owner || '',
    priority: task.priority,
    project: task.project || '',
    recurrence: task.recurrence || '',
    recurrenceRule: task.recurrenceRule || '',
    recurrenceTimezone: task.recurrenceTimezone || localTimeZone,
    reviewDate: task.reviewDate || '',
    title: task.title,
    waitingOn: task.waitingOn || ''
  }
}

function taskDetailValues(draft) {
  const recurrenceRule = draft.recurrenceRule.trim() || null
  return {
    approvalState: draft.approvalState,
    artefacts: draft.artefacts,
    blocker: draft.blocker.trim() || null,
    brief: draft.brief.trim() || null,
    closureCondition: draft.closureCondition.trim() || null,
    closureEvidence: draft.closureEvidence,
    closureNote: draft.closureNote.trim() || null,
    executionMode: draft.executionMode,
    nextAction: draft.nextAction.trim() || null,
    owner: draft.owner.trim() || null,
    priority: draft.priority || null,
    project: draft.project.trim() || null,
    recurrence: draft.recurrence.trim() || null,
    recurrenceRule,
    recurrenceTimezone: recurrenceRule ? draft.recurrenceTimezone.trim() || 'UTC' : null,
    reviewDate: draft.reviewDate || null,
    title: draft.title.trim(),
    waitingOn: draft.waitingOn.trim() || null
  }
}

function sameTaskDetailValue(left, right) {
  if (!Array.isArray(left) || !Array.isArray(right)) return Object.is(left, right)
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function changedTaskDetails(initial, current) {
  const initialValues = taskDetailValues(initial)
  const currentValues = taskDetailValues(current)
  const changes = {}
  for (const [field, value] of Object.entries(currentValues)) {
    if (!sameTaskDetailValue(initialValues[field], value)) changes[field] = value
  }

  const dueWasEdited = initial.dueMode !== current.dueMode ||
    initial.dueDate !== current.dueDate ||
    initial.dueAt !== current.dueAt
  if (dueWasEdited) {
    if (current.dueMode === 'timed') {
      changes.dueAt = current.dueAt || null
      changes.dueDate = null
      changes.dueTimezone = current.dueAt ? current.dueTimezone : null
    } else {
      changes.dueDate = current.dueDate || null
      changes.dueAt = null
      changes.dueTimezone = null
    }
  }
  return changes
}

function TaskDetails({ ctx, task, disabled, update, remove, close, completeSession }) {
  const activeProfile = useValue(host.state.profile)
  const initialDraftRef = useRef(null)
  if (initialDraftRef.current === null) {
    const localTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
    initialDraftRef.current = makeTaskDetailsDraft(task, localTimeZone)
  }
  const initialDraft = initialDraftRef.current
  const [titleDraft, setTitleDraft] = useState(initialDraft.title)
  const [projectDraft, setProjectDraft] = useState(initialDraft.project)
  const [recurrenceDraft, setRecurrenceDraft] = useState(initialDraft.recurrence)
  const [recurrenceRuleDraft, setRecurrenceRuleDraft] = useState(initialDraft.recurrenceRule)
  const [recurrenceTimezoneDraft, setRecurrenceTimezoneDraft] = useState(initialDraft.recurrenceTimezone)
  const [briefDraft, setBriefDraft] = useState(initialDraft.brief)
  const [nextActionDraft, setNextActionDraft] = useState(initialDraft.nextAction)
  const [closureConditionDraft, setClosureConditionDraft] = useState(initialDraft.closureCondition)
  const [waitingOnDraft, setWaitingOnDraft] = useState(initialDraft.waitingOn)
  const [reviewDateDraft, setReviewDateDraft] = useState(initialDraft.reviewDate)
  const [blockerDraft, setBlockerDraft] = useState(initialDraft.blocker)
  const [artefactsDraft, setArtefactsDraft] = useState(initialDraft.artefacts.join('\n'))
  const [ownerDraft, setOwnerDraft] = useState(initialDraft.owner)
  const [executionModeDraft, setExecutionModeDraft] = useState(initialDraft.executionMode)
  const [approvalStateDraft, setApprovalStateDraft] = useState(initialDraft.approvalState)
  const [closureNoteDraft, setClosureNoteDraft] = useState(initialDraft.closureNote)
  const [closureEvidenceDraft, setClosureEvidenceDraft] = useState(initialDraft.closureEvidence.join('\n'))
  const [priorityDraft, setPriorityDraft] = useState(initialDraft.priority)
  const [dueMode, setDueMode] = useState(initialDraft.dueMode)
  const [dueDraft, setDueDraft] = useState(initialDraft.dueDate)
  const [timedDraft, setTimedDraft] = useState(initialDraft.dueAt)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const historyQuery = useQuery({
    queryKey: [ID, 'history', activeProfile || 'default', task.id],
    queryFn: async () => sharedBoardRest(ctx, `/tasks/${encodeURIComponent(task.id)}/history?limit=40`, { timeoutMs: 5000 }),
    retry: 1
  })

  const saveDetails = async event => {
    event.preventDefault()
    const title = titleDraft.trim()
    if (!title) return
    const currentDraft = {
      approvalState: approvalStateDraft,
      artefacts: lines(artefactsDraft),
      blocker: blockerDraft,
      brief: briefDraft,
      closureCondition: closureConditionDraft,
      closureEvidence: lines(closureEvidenceDraft),
      closureNote: closureNoteDraft,
      dueAt: timedDraft,
      dueDate: dueDraft,
      dueMode,
      dueTimezone: initialDraft.dueTimezone,
      executionMode: executionModeDraft,
      nextAction: nextActionDraft,
      owner: ownerDraft,
      priority: priorityDraft,
      project: projectDraft,
      recurrence: recurrenceDraft,
      recurrenceRule: recurrenceRuleDraft,
      recurrenceTimezone: recurrenceTimezoneDraft,
      reviewDate: reviewDateDraft,
      title,
      waitingOn: waitingOnDraft
    }
    const changes = changedTaskDetails(initialDraft, currentDraft)
    if (Object.keys(changes).length === 0) {
      close()
      return
    }
    const saved = await update(task.id, changes)
    if (saved) close()
  }

  return jsxs('form', {
    className: 'mt-2 rounded-md border border-(--ui-stroke-secondary) p-2',
    onSubmit: event => void saveDetails(event),
    onKeyDown: event => {
      if (event.key === 'Escape') close()
    },
    children: [
      jsx(FieldLabel, { children: 'Task title' }),
      jsx(Input, {
        'aria-label': 'Task title',
        className: 'h-7 text-xs',
        disabled,
        maxLength: 500,
        onChange: event => setTitleDraft(event.target.value),
        value: titleDraft
      }),
      jsx(FieldLabel, { children: 'Plan' }),
      jsxs('div', {
        className: 'flex flex-wrap gap-1',
        children: [
          jsx(ChoiceButton, { active: task.status === 'open' && task.plan === 'now', disabled, onClick: () => void update(task.id, { plan: 'now', status: 'open', inbox: false, waitingOn: null, reviewDate: null, blocker: null }), children: 'Now' }),
          jsx(ChoiceButton, { active: task.status === 'open' && task.plan === 'today', disabled, onClick: () => void update(task.id, { plan: 'today', status: 'open', inbox: false, waitingOn: null, reviewDate: null, blocker: null }), children: 'Today' }),
          jsx(ChoiceButton, { active: task.status === 'open' && task.plan === 'later', disabled, onClick: () => void update(task.id, { plan: 'later', status: 'open', inbox: false, waitingOn: null, reviewDate: null, blocker: null }), children: 'Later' })
        ]
      }),
      jsx(FieldLabel, { children: 'Status' }),
      jsxs('div', {
        className: 'flex flex-wrap gap-1',
        children: [
          jsx(ChoiceButton, { active: task.status === 'open', disabled, onClick: () => void update(task.id, { status: 'open', waitingOn: null, reviewDate: null, blocker: null }), children: 'Open' }),
          jsx(ChoiceButton, { active: task.status === 'waiting', disabled, onClick: () => void update(task.id, { status: 'waiting', blocker: null }), children: 'Waiting' }),
          jsx(ChoiceButton, { active: task.status === 'blocked', disabled, onClick: () => void update(task.id, { status: 'blocked', waitingOn: null, reviewDate: null }), children: 'Blocked' })
        ]
      }),
      jsx(FieldLabel, { children: 'Deadline' }),
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
      jsx(TextAreaField, { disabled, label: 'Brief and decisions', maxLength: 8000, onChange: event => setBriefDraft(event.target.value), placeholder: 'Durable context for re-entry', value: briefDraft }),
      jsx(TextAreaField, { disabled, label: 'Next action', maxLength: 2000, onChange: event => setNextActionDraft(event.target.value), placeholder: 'The smallest live move', value: nextActionDraft }),
      jsx(TextAreaField, { disabled, label: 'Closure condition', maxLength: 4000, onChange: event => setClosureConditionDraft(event.target.value), placeholder: 'What proves this is complete?', value: closureConditionDraft }),
      jsx(FieldLabel, { children: 'Waiting and blocking' }),
      jsxs('div', {
        className: 'grid grid-cols-2 gap-1.5',
        children: [
          jsx(Input, { 'aria-label': 'Waiting on', className: 'h-7 text-xs', disabled, maxLength: 1000, onChange: event => setWaitingOnDraft(event.target.value), placeholder: 'Waiting on', value: waitingOnDraft }),
          jsx(Input, { 'aria-label': 'Review date', className: 'h-7 text-xs', disabled, onChange: event => setReviewDateDraft(event.target.value), type: 'date', value: reviewDateDraft })
        ]
      }),
      jsx(TextAreaField, { disabled, label: 'Blocker', maxLength: 2000, onChange: event => setBlockerDraft(event.target.value), placeholder: 'What prevents progress?', value: blockerDraft }),
      jsx(FieldLabel, { children: 'Project' }),
      jsx(Input, {
        'aria-label': 'Project',
        className: 'h-7 text-xs',
        disabled,
        maxLength: 500,
        onChange: event => setProjectDraft(event.target.value),
        placeholder: 'Optional',
        value: projectDraft
      }),
      jsx(FieldLabel, { children: 'Priority' }),
      jsx('div', {
        className: 'flex flex-wrap gap-1',
        children: [null, 1, 2, 3, 4].map(value => jsx(ChoiceButton, {
          active: priorityDraft === value,
          disabled,
          onClick: () => setPriorityDraft(value),
          children: value === null ? 'None' : `P${value}`
        }, String(value)))
      }),
      jsx(FieldLabel, { children: 'Owner and execution' }),
      jsx(Input, { 'aria-label': 'Owner', className: 'h-7 text-xs', disabled, maxLength: 200, onChange: event => setOwnerDraft(event.target.value), placeholder: 'Owner or assignee', value: ownerDraft }),
      jsx('div', {
        className: 'mt-1 flex flex-wrap gap-1',
        children: ['manual', 'supervised', 'autonomous'].map(value => jsx(ChoiceButton, { active: executionModeDraft === value, disabled, onClick: () => setExecutionModeDraft(value), children: value }, value))
      }),
      jsx('div', {
        className: 'mt-1 flex flex-wrap gap-1',
        children: ['not-required', 'pending', 'approved', 'rejected'].map(value => jsx(ChoiceButton, { active: approvalStateDraft === value, disabled, onClick: () => setApprovalStateDraft(value), children: value }, value))
      }),
      jsx(TextAreaField, { disabled, label: 'Artefacts', maxLength: 20000, onChange: event => setArtefactsDraft(event.target.value), placeholder: 'One safe link or path per line', value: artefactsDraft }),
      jsx(FieldLabel, { children: 'Recurrence note' }),
      jsx(Input, {
        'aria-label': 'Recurrence note',
        className: 'h-7 text-xs',
        disabled,
        maxLength: 500,
        onChange: event => setRecurrenceDraft(event.target.value),
        placeholder: 'Optional',
        value: recurrenceDraft
      }),
      jsx(FieldLabel, { children: 'Executable recurrence' }),
      jsxs('div', {
        className: 'grid grid-cols-2 gap-1.5',
        children: [
          jsx(Input, { 'aria-label': 'Recurrence rule', className: 'h-7 text-xs', disabled, maxLength: 50, onChange: event => setRecurrenceRuleDraft(event.target.value), placeholder: 'daily, weekdays, weekly…', value: recurrenceRuleDraft }),
          jsx(Input, { 'aria-label': 'Recurrence timezone', className: 'h-7 text-xs', disabled, maxLength: 100, onChange: event => setRecurrenceTimezoneDraft(event.target.value), placeholder: 'Europe/Amsterdam', value: recurrenceTimezoneDraft })
        ]
      }),
      task.seriesId && jsx('div', { className: 'mt-1 break-all text-[0.625rem] text-(--ui-text-quaternary)', children: `Series ${task.seriesId} · occurrence ${task.occurrenceNumber}` }),
      jsx(TextAreaField, { disabled, label: 'Closure note', maxLength: 4000, onChange: event => setClosureNoteDraft(event.target.value), placeholder: 'What was delivered?', value: closureNoteDraft }),
      jsx(TextAreaField, { disabled, label: 'Closure evidence', maxLength: 20000, onChange: event => setClosureEvidenceDraft(event.target.value), placeholder: 'One verification path or link per line', value: closureEvidenceDraft }),
      task.sessionId && jsxs('div', {
        className: 'mt-2 rounded-md border border-(--ui-stroke-secondary) px-2 py-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
        children: [
          jsx('div', { className: 'break-all', children: `Hermes session ${task.sessionState || 'linked'} · ${task.sessionId}` }),
          task.sessionState === 'active' && jsxs('div', {
            className: 'mt-1.5 flex items-center justify-between gap-2',
            children: [
              jsx('span', { className: 'leading-4 text-(--ui-text-quaternary)', children: 'Close a stale active link before starting a replacement.' }),
              jsx(Button, {
                disabled,
                onClick: () => void completeSession(task.id),
                size: 'micro',
                type: 'button',
                variant: 'text',
                children: 'Close linked session'
              })
            ]
          })
        ]
      }),
      jsxs('details', {
        className: 'mt-3',
        children: [
          jsxs('summary', {
            className: 'cursor-pointer text-[0.6875rem] font-medium text-(--ui-text-tertiary)',
            children: ['History', historyQuery.data?.events?.length ? ` · ${historyQuery.data.events.length}` : '']
          }),
          historyQuery.isLoading
            ? jsx('div', { className: 'py-2 text-[0.6875rem] text-(--ui-text-quaternary)', children: 'Loading history…' })
            : historyQuery.isError
              ? jsx('div', { className: 'py-2 text-[0.6875rem] text-(--ui-text-tertiary)', children: `History unavailable: ${errorText(historyQuery.error)}` })
              : historyQuery.data?.events?.length
                ? jsx('ol', {
                    className: 'mt-1 border-l border-(--ui-stroke-secondary) pl-2',
                    children: historyQuery.data.events.map(event => jsxs('li', {
                      className: 'py-1 text-[0.625rem] leading-4 text-(--ui-text-quaternary)',
                      children: [
                        jsx('div', { className: 'font-medium text-(--ui-text-tertiary)', children: event.type.replaceAll('.', ' ') }),
                        jsx('div', { children: [event.source, event.actor, new Date(event.createdAt).toLocaleString()].filter(Boolean).join(' · ') })
                      ]
                    }, event.id))
                  })
                : jsx('div', { className: 'py-2 text-[0.6875rem] text-(--ui-text-quaternary)', children: 'No history recorded yet.' })
        ]
      }),
      jsx('div', {
        className: 'mt-2 flex items-center gap-1',
        children: [
          jsx(Button, { disabled: disabled || !titleDraft.trim(), size: 'xs', type: 'submit', variant: 'secondary', children: 'Save details' }),
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

function TaskRow({ ctx, task, update, remove, completeSession, cycleEstimate, pending, workingId, workWithHermes, prominent = false, reason }) {
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
              (reason || due || task.project || task.priority || task.owner || task.nextAction || task.recurrence || task.inbox) && jsx('div', {
                className: cn(
                  'mt-0.5 truncate text-[0.625rem] text-(--ui-text-quaternary)',
                  due?.startsWith('Overdue') && 'font-medium text-(--ui-text-secondary)'
                ),
                children: [reason, task.inbox ? 'Inbox' : null, due, task.project, task.owner, task.priority ? `P${task.priority}` : null, task.nextAction, task.recurrenceRule || task.recurrence].filter(Boolean).join(' · ')
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
                ? jsx(IconButton, { disabled, icon: icons.RefreshCw, label: 'Reopen', onClick: () => void update(task.id, { status: 'open', waitingOn: null, reviewDate: null, blocker: null }) })
                : jsx(IconButton, { disabled, icon: icons.Check, label: 'Complete', onClick: () => {
                    haptic('success')
                    void update(task.id, { status: 'done' })
                  } })
            ]
          })
        ]
      }),
      editing && jsx(TaskDetails, { close: () => setEditing(false), completeSession, ctx, disabled, remove, task, update }),
      task.status !== 'done' && jsxs('div', {
        className: 'mt-1.5 flex flex-wrap items-center gap-1',
        children: [
          task.status === 'open' && (task.plan !== 'now' || task.inbox) && jsx(Button, {
            disabled,
            onClick: () => void update(task.id, { plan: 'now', status: 'open', inbox: false, waitingOn: null, reviewDate: null, blocker: null }),
            size: 'xs',
            type: 'button',
            variant: 'secondary',
            children: 'Start now'
          }),
          (task.status === 'open' || (task.sessionId && task.sessionState === 'active')) && jsx(Button, {
            className: cn(prominent ? '' : 'opacity-80 group-hover:opacity-100'),
            disabled,
            onClick: () => void workWithHermes(task),
            size: 'xs',
            type: 'button',
            variant: prominent ? 'default' : 'secondary',
            children: jsxs('span', {
              className: 'inline-flex items-center gap-1',
              children: [
                jsx(icons.MessageCircle, { className: 'size-3' }),
                workingId === task.id
                  ? 'Sending…'
                  : task.sessionId && task.sessionState === 'active'
                    ? task.status === 'open' ? 'Resume with Hermes' : 'Open linked session'
                    : 'Work with Hermes'
              ]
            })
          }),
          ['waiting', 'blocked'].includes(task.status) && !(task.sessionId && task.sessionState === 'active') && jsx('span', {
            className: 'text-[0.625rem] text-(--ui-text-quaternary)',
            children: task.status === 'blocked' ? 'Clear the blocker to start Hermes.' : 'Reopen when the wait is over.'
          })
        ]
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

function BoardView({ remote, rowProps, sections }) {
  return jsxs('div', {
    children: [
      jsx(Section, {
        count: sections.inbox.length,
        title: 'Inbox',
        children: sections.inbox.length
          ? sections.inbox.map(task => jsx(TaskRow, { ...rowProps, pending: remote.pendingIds.has(task.id), task }, task.id))
          : jsx('div', { className: 'py-1 text-[0.6875rem] text-(--ui-text-quaternary)', children: 'Captured tasks wait here until you start or plan them.' })
      }),
      jsx(Section, {
        count: sections.now.length,
        title: 'Now',
        children: sections.now.length
          ? sections.now.map(task => jsx(TaskRow, { ...rowProps, pending: remote.pendingIds.has(task.id), prominent: true, task }, task.id))
          : jsx('div', {
              className: 'border-l-2 border-l-(--ui-stroke-secondary) py-2 pl-2.5 text-xs leading-5 text-(--ui-text-quaternary)',
              children: 'Nothing is running. Choose Start now when you are ready.'
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
    const grouped = { inbox: [], now: [], today: [], later: [], waiting: [], blocked: [], done: [] }
    const needle = filter.trim().toLocaleLowerCase()
    for (const task of remote.board.tasks) {
      const haystack = [task.title, task.project, task.recurrence, task.recurrenceRule, task.brief, task.nextAction, task.owner, task.waitingOn, task.blocker].filter(Boolean).join(' ').toLocaleLowerCase()
      if (!needle || haystack.includes(needle)) grouped[sectionFor(task, todayKey)].push(task)
    }
    const dueThenCreated = (a, b) => {
      const aPriority = a.priority || 99
      const bPriority = b.priority || 99
      const aDue = a.dueDate || a.dueAt || '9999'
      const bDue = b.dueDate || b.dueAt || '9999'
      return aPriority - bPriority || aDue.localeCompare(bDue) || timeValue(a.createdAt) - timeValue(b.createdAt)
    }
    for (const key of ['inbox', 'now', 'today', 'later', 'waiting', 'blocked']) grouped[key].sort(dueThenCreated)
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
    const added = await remote.add(title.slice(0, 500))
    if (added) {
      setDraft('')
      haptic('success')
    }
  }

  const workWithHermes = useCallback(
    async task => {
      if (workPending.current.has(task.id)) return
      if (task.sessionId && task.sessionState === 'active') {
        host.navigate(`/${encodeURIComponent(task.sessionId)}`)
        return
      }
      if (task.status !== 'open') {
        host.notify({ kind: 'warning', message: 'Reopen and clear the waiting or blocking context before starting Hermes.' })
        return
      }
      if (host.state.gateway.get() !== 'open') {
        host.notify({ kind: 'warning', message: 'Connect Hermes before starting this task.' })
        return
      }
      workPending.current.add(task.id)
      setWorkingId(task.id)
      let createdSession = null
      let linked = false
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
        linked = await remote.linkSession(task.id, createdSession.stored_session_id)
        if (!linked) {
          await host.request('session.close', { session_id: createdSession.session_id }).catch(() => undefined)
          return
        }
        await host.request('prompt.submit', { session_id: createdSession.session_id, text: buildWorkPrompt({ ...task, plan: 'now', inbox: false, sessionId: createdSession.stored_session_id, sessionState: 'active' }) })
        host.navigate(`/${encodeURIComponent(createdSession.stored_session_id)}`)
      } catch (error) {
        if (createdSession?.session_id) {
          await host.request('session.close', { session_id: createdSession.session_id }).catch(() => undefined)
        }
        if (linked) await remote.completeSession(task.id)
        host.notifyError(error, 'Could not send this task to Hermes')
      } finally {
        workPending.current.delete(task.id)
        setWorkingId(null)
      }
    },
    [remote]
  )

  const connectionLabel = remote.connection === 'online'
    ? 'v0.2.0 · Shared with Hermes'
    : remote.connection === 'connecting'
      ? 'v0.2.0 · Connecting…'
      : `v0.2.0 · Offline: ${remote.error || 'request failed'}`

  const rowProps = {
    completeSession: remote.completeSession,
    ctx,
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
                'aria-label': 'Capture a task to Inbox',
                className: 'min-w-0 flex-1',
                disabled: remote.connection === 'offline',
                maxLength: 500,
                onChange: event => setDraft(event.target.value),
                placeholder: 'Capture to Inbox',
                value: draft
              }),
              jsx(Button, {
                'aria-label': 'Capture task',
                disabled: !draft.trim() || remote.adding || remote.connection === 'offline',
                size: 'sm',
                type: 'submit',
                children: remote.adding ? 'Capturing…' : 'Capture'
              })
            ]
          }),
          remote.connection === 'offline' && jsxs('div', {
            className: 'mt-2 flex items-start justify-between gap-2 rounded-md border border-(--ui-stroke-secondary) px-2 py-1.5',
            children: [
              jsx('span', { className: 'text-[0.6875rem] leading-4 text-(--ui-text-tertiary)', children: `Shared board unavailable: ${remote.error || 'request failed'}` }),
              jsx(Button, { onClick: () => void remote.refresh(), size: 'micro', type: 'button', variant: 'text', children: 'Retry' })
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
        'aria-busy': remote.connection === 'connecting',
        children: jsx('div', {
          className: 'w-full min-w-0 max-w-full px-3 py-3',
          children: remote.connection === 'connecting' && !remote.board.tasks.length
            ? jsx(EmptyState, { className: 'min-h-32 py-5', description: 'Loading the profile-scoped Todo board.', title: 'Connecting…' })
            : jsx(BoardView, { remote, rowProps, sections })
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
