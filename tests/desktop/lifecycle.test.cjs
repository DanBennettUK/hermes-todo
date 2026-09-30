const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const React = require('react')
const { jsx, jsxs } = require('react/jsx-runtime')
const { create, act } = require('react-test-renderer')

function setup() {
  const timers = new Map(), listeners = new Map()
  let nextTimer = 0
  const old = { setTimeout, clearTimeout, window: global.window, document: global.document }
  global.setTimeout = (fn, ms) => { timers.set(++nextTimer, { fn, ms }); return nextTimer }
  global.clearTimeout = id => timers.delete(id)
  global.window = {
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn) },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn) }
  }
  const body = { style: { userSelect: '' }, appendChild(node) { node.parentNode = body }, removeChild(node) { node.parentNode = null } }
  global.document = { body, createElement: () => ({ style: {}, parentNode: null }), querySelector: () => null, querySelectorAll: () => [] }
  const widgets = tag => ({ children, ...props }) => React.createElement(tag, {
    onClick: props.onClick, onChange: props.onChange, onKeyDown: props.onKeyDown,
    disabled: props.disabled, value: props.value, type: props.type,
    'aria-label': props['aria-label'], 'aria-expanded': props['aria-expanded']
  }, children)
  const panes = new Map(), registrations = [], notices = []
  const host = {
    state: { profile: { get: () => 'test' } },
    notify: message => notices.push(message), notifyError: error => notices.push(error),
    openWorkspace(id, options) {
      panes.set(id, options)
      return () => { panes.delete(id); options.onClose?.() }
    }
  }
  const sdk = {
    Button: widgets('button'), Input: widgets('input'), EmptyState: widgets('div'),
    SegmentedControl: widgets('nav'), Tip: ({ children }) => children,
    PALETTE_AREA: 'palette', cn: (...values) => values.filter(Boolean).join(' '), haptic() {}, host,
    icons: new Proxy({}, { get: (_, name) => () => jsx('span', { 'data-icon': name }) }),
    useValue: value => value.get(), useQuery: () => ({ data: { events: [] }, isPending: false }), useQueryClient() {}
  }
  const source = fs.readFileSync(process.env.HERMES_TODO_TEST_SOURCE || path.resolve(__dirname, '../../desktop-plugin/hermes-todo/plugin.js'), 'utf8')
    .replace(/import[\s\S]*?from ['"][^'"]+['"]\s*/g, '')
    .replace('export default {', 'const plugin = {')
  const scope = { ...sdk, ...React, jsx, jsxs }
  const code = new Function(...Object.keys(scope), source + '\nreturn { TaskRow, TaskDetails, SubtaskEditor, plugin, dragPointerState }')
  const loaded = code(...Object.values(scope))
  const roots = []
  return {
    ...loaded, sdk, panes, registrations, notices, listeners, timers,
    mount(component, props) { let root; act(() => { root = create(jsx(component, props)) }); roots.push(root); return root },
    fire(type, event) { act(() => { for (const fn of [...(listeners.get(type) || [])]) fn(event) }) },
    tick(ms) { act(() => { for (const [id, timer] of [...timers]) if (timer.ms === ms) { timers.delete(id); timer.fn() } }) },
    cleanup() {
      act(() => roots.forEach(root => root.unmount()))
      global.setTimeout = old.setTimeout; global.clearTimeout = old.clearTimeout
      global.window = old.window; global.document = old.document
    }
  }
}
const task = { id: 't', title: 'Original', status: 'open', plan: 'today', category: 'today', estimate: 25, subtasks: [] }
const button = (root, label) => root.root.findAllByType('button').find(node => node.props['aria-label'] === label || node.props.children === label)
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const settle = async () => { await act(async () => { await Promise.resolve() }) }

test('mounted completion arms, confirms once, expires and cancels', async () => {
  const env = setup()
  try {
    const writes = []
    const root = env.mount(env.TaskRow, { task, update: (...args) => writes.push(args) })
    act(() => button(root, 'Complete').props.onClick())
    assert.equal(writes.length, 0)
    act(() => button(root, 'Confirm complete').props.onClick())
    assert.deepEqual(writes, [['t', { status: 'done' }]])
    act(() => button(root, 'Complete').props.onClick())
    env.tick(3000)
    assert.ok(button(root, 'Complete'))
    act(() => button(root, 'Complete').props.onClick())
    env.fire('keydown', { key: 'Escape' })
    assert.ok(button(root, 'Complete'))
    act(() => button(root, 'Complete').props.onClick())
    env.fire('pointerdown', { target: { closest: () => null } })
    assert.ok(button(root, 'Complete'))
    act(() => root.update(jsx(env.TaskRow, { task, pending: true, update: () => {} })))
    assert.equal(button(root, 'Complete').props.disabled, true)
  } finally { env.cleanup() }
})

test('mounted editor drains latest draft on unmount using acknowledged revisions', async () => {
  const env = setup()
  try {
    const first = deferred(), second = deferred(), writes = []
    const update = (...args) => { writes.push(args); return writes.length === 1 ? first.promise : second.promise }
    const root = env.mount(env.TaskDetails, { task, boardRevision: 4, update })
    const input = () => root.root.findAllByType('input').find(node => node.props['aria-label'] === 'Task title')
    act(() => input().props.onChange({ target: { value: 'First edit' } }))
    act(() => button(root, 'Save').props.onClick())
    act(() => input().props.onChange({ target: { value: 'Second edit' } }))
    act(() => root.unmount())
    first.resolve({ ok: true, revision: 5 }); await settle()
    assert.equal(writes.length, 2)
    assert.deepEqual(writes.map(args => [args[1].title, args[2]]), [['First edit', 4], ['Second edit', 5]])
    second.resolve({ ok: true, revision: 6 }); await settle()
    assert.equal(writes.length, 2)
  } finally { env.cleanup() }
})

test('mounted editor keeps failed draft and opening revision despite polling', async () => {
  const env = setup()
  try {
    const writes = [], first = deferred()
    const update = (...args) => { writes.push(args); return writes.length === 1 ? first.promise : Promise.resolve({ ok: true, revision: 5 }) }
    const root = env.mount(env.TaskDetails, { task, boardRevision: 4, update })
    const input = root.root.findAllByType('input').find(node => node.props['aria-label'] === 'Task title')
    act(() => input.props.onChange({ target: { value: 'Unsaved' } }))
    act(() => root.update(jsx(env.TaskDetails, { task, boardRevision: 99, update })))
    act(() => button(root, 'Save').props.onClick())
    first.resolve(false); await settle()
    act(() => button(root, 'Save').props.onClick()); await settle()
    assert.equal(writes.length, 2)
    assert.deepEqual(writes.map(args => [args[1].title, args[2]]), [['Unsaved', 4], ['Unsaved', 4]])
  } finally { env.cleanup() }
})

test('mounted editor blocks autosave during deletion and restores it on failure', async () => {
  const env = setup()
  try {
    const deletion = deferred(), writes = []
    const root = env.mount(env.TaskDetails, { task, boardRevision: 1, update: (...args) => { writes.push(args); return Promise.resolve({ ok: true, revision: 2 }) }, remove: () => deletion.promise })
    const input = root.root.findAllByType('input').find(node => node.props['aria-label'] === 'Task title')
    act(() => input.props.onChange({ target: { value: 'Dirty' } }))
    act(() => { void button(root, 'Delete').props.onClick() })
    act(() => { void button(root, 'Confirm delete').props.onClick() })
    env.tick(500); await settle()
    assert.equal(writes.length, 0)
    deletion.resolve(false); await settle()
    act(() => button(root, 'Save').props.onClick()); await settle()
    assert.equal(writes.length, 1)
  } finally { env.cleanup() }
})

test('mounted editor stays open with its draft when close-save fails', async () => {
  const env = setup()
  try {
    let closed = 0
    const root = env.mount(env.TaskDetails, { task, boardRevision: 4, update: () => Promise.resolve(false), close: () => { closed++ } })
    const input = root.root.findAllByType('input').find(node => node.props['aria-label'] === 'Task title')
    act(() => input.props.onChange({ target: { value: 'Keep me' } }))
    act(() => { button(root, 'Cancel').props.onClick() }); await settle()
    assert.equal(closed, 0)
    assert.equal(root.root.findAllByType('input').find(node => node.props['aria-label'] === 'Task title').props.value, 'Keep me')
  } finally { env.cleanup() }
})

test('mounted row unmount disarms both armed and active drag', () => {
  for (const active of [false, true]) {
    const env = setup()
    try {
      const root = env.mount(env.TaskRow, { task, update() {} })
      const row = root.root.findAllByType('div').find(node => node.props.onPointerDown)
      act(() => row.props.onPointerDown({ button: 0, pointerId: 7, clientX: 0, clientY: 0, target: { closest: () => null }, currentTarget: { getBoundingClientRect: () => ({ left: 0 }) } }))
      if (active) env.fire('pointermove', { pointerId: 7, clientX: 10, clientY: 10 })
      const ghost = env.dragPointerState.ghost
      act(() => root.unmount())
      assert.equal([...env.listeners.values()].reduce((sum, list) => sum + list.size, 0), 0)
      assert.equal(env.dragPointerState.task, null)
      assert.equal(document.body.style.userSelect, '')
      if (ghost) assert.equal(ghost.parentNode, null)
    } finally { env.cleanup() }
  }
})

test('workspace registration closes, reopens and disposes with one pane in the SDK double', () => {
  const env = setup()
  try {
    let dispose
    env.plugin.register({ register: entry => env.registrations.push(entry), onDispose: fn => { dispose = fn } })
    assert.equal(env.panes.size, 1)
    const pane = env.panes.get('todo')
    env.panes.delete('todo'); pane.onClose()
    assert.equal(env.panes.size, 0)
    const command = env.registrations.find(entry => entry.id === 'open').data.run
    command(); command()
    assert.equal(env.panes.size, 1)
    dispose()
    assert.equal(env.panes.size, 0)
  } finally { env.cleanup() }
})

test('mounted task and subtask reorder controls expose keyboard-operable actions', async () => {
  const env = setup()
  try {
    const writes = [], before = { ...task, id: 'before' }, after = { ...task, id: 'after' }
    const root = env.mount(env.TaskRow, { task, categoryTasks: [before, task, after], reorder: (...args) => { writes.push(args); return Promise.resolve(true) } })
    act(() => { button(root, 'Move task earlier').props.onClick() }); await settle()
    act(() => { button(root, 'Move task later').props.onClick() }); await settle()
    assert.deepEqual(writes, [['t', { category: 'today', beforeId: 'before' }], ['t', { category: 'today', afterId: 'after' }]])
    assert.equal(env.notices.length, 2)
    const items = [{ id: 'a', title: 'A', done: false }, { id: 'b', title: 'B', done: false }]
    const subRoot = env.mount(env.SubtaskEditor, { task: { ...task, subtasks: items }, reorderSubtask: (...args) => writes.push(args) })
    assert.equal(button(subRoot, 'Move subtask A earlier').props.disabled, true)
    act(() => { button(subRoot, 'Move subtask A later').props.onClick() })
    assert.deepEqual(writes.at(-1), ['t', 'a', { afterId: 'b' }])
  } finally { env.cleanup() }
})

test('mounted subtask drag cleans up and sends a single non-self anchor', () => {
  for (const outcome of ['unmount', 'cancel', 'escape', 'blur', 'drop', 'click']) {
    const env = setup()
    try {
      const writes = [], items = [{ id: 'a', title: 'A', done: false }, { id: 'b', title: 'B', done: false }]
      const root = env.mount(env.SubtaskEditor, { task: { ...task, subtasks: items }, reorderSubtask: (...args) => writes.push(args) })
      const handle = root.root.findAllByType('button').find(node => node.props['aria-label'] === 'Reorder subtask')
      act(() => handle.props.onPointerDown({ button: 0, pointerId: 8, clientY: 0, preventDefault() {}, stopPropagation() {} }))
      if (outcome !== 'click') env.fire('pointermove', { pointerId: 8, clientY: 20 })
      document.elementFromPoint = () => ({ closest: () => ({ getAttribute: () => 'b', getBoundingClientRect: () => ({ top: 0, height: 10 }) }) })
      if (outcome === 'unmount') act(() => root.unmount())
      else if (outcome === 'cancel') env.fire('pointercancel', { pointerId: 8 })
      else if (outcome === 'escape') env.fire('keydown', { key: 'Escape' })
      else if (outcome === 'blur') env.fire('blur', {})
      else env.fire('pointerup', { pointerId: 8, clientX: 0, clientY: 20 })
      assert.equal([...env.listeners.values()].reduce((sum, list) => sum + list.size, 0), 0)
      assert.deepEqual(writes, outcome === 'drop' ? [['t', 'a', { afterId: 'b' }]] : [])
    } finally { env.cleanup() }
  }
})
