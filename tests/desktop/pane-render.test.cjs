const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('path')
const React = require('react')
const { jsx, jsxs } = require('react/jsx-runtime')
const { create, act } = require('react-test-renderer')

// Names exported by Hermes Desktop apps/desktop/src/lib/icons.ts.
// ArrowDown is not one of them. A Proxy would hide that and let CI pass.
const HOST_ICONS = [
  'ArrowBarToDown',
  'ArrowBarToUp',
  'ArrowUp',
  'ArrowUpRight',
  'Check',
  'CheckCircle2',
  'ChevronDown',
  'ChevronRight',
  'ChevronUp',
  'MessageCircle',
  'MoreHorizontal',
  'RefreshCw',
  'Save',
  'X'
]

function setup(board) {
  const old = { setTimeout, clearTimeout, window: global.window, document: global.document }
  global.window = {
    addEventListener() {},
    removeEventListener() {}
  }
  const body = {
    style: { userSelect: '' },
    appendChild(node) { node.parentNode = body },
    removeChild(node) { node.parentNode = null }
  }
  global.document = {
    body,
    createElement: () => ({ style: {}, parentNode: null }),
    querySelector: () => null,
    querySelectorAll: () => []
  }
  const widgets = tag => ({ children, ...props }) => React.createElement(tag, {
    onClick: props.onClick,
    onChange: props.onChange,
    disabled: props.disabled,
    value: props.value,
    type: props.type,
    'aria-label': props['aria-label']
  }, children)
  const icons = {}
  for (const name of HOST_ICONS) {
    icons[name] = () => jsx('span', { 'data-icon': name })
  }
  const host = {
    state: {
      profile: { get: () => 'work' },
      gateway: { get: () => 'open' },
      activeSessionId: { get: () => 'session-1' },
      cwd: { get: () => '' },
      model: { get: () => '' }
    },
    notify() {},
    notifyError() {},
    openWorkspace(id, options) {
      return () => options.onClose?.()
    }
  }
  const stored = new Map()
  const ctx = {
    storage: {
      get(key, fallback) { return stored.has(key) ? stored.get(key) : fallback },
      set(key, value) { stored.set(key, value) }
    },
    rest: async () => board
  }
  const sdk = {
    Button: widgets('button'),
    Input: widgets('input'),
    EmptyState: widgets('div'),
    SegmentedControl: widgets('nav'),
    Tip: ({ children }) => children,
    PALETTE_AREA: 'palette',
    cn: (...values) => values.filter(Boolean).join(' '),
    haptic() {},
    host,
    icons,
    useValue: value => value.get(),
    useQuery: () => ({
      data: board,
      isPending: false,
      isSuccess: true,
      isError: false,
      error: null,
      refetch: async () => board
    }),
    useQueryClient() {
      return { setQueryData() {}, cancelQueries() {}, invalidateQueries() {} }
    }
  }
  const source = fs.readFileSync(path.resolve(__dirname, '../../desktop-plugin/hermes-todo/plugin.js'), 'utf8')
    .replace(/import[\s\S]*?from ['"][^'"]+['"]\s*/g, '')
    .replace('export default {', 'const plugin = {')
  const scope = { ...sdk, ...React, jsx, jsxs }
  const code = new Function(...Object.keys(scope), `${source}\nreturn { TodoPane, SubtaskEditor, plugin }`)
  const loaded = code(...Object.values(scope))
  const roots = []
  return {
    ...loaded,
    ctx,
    mount(component, props) {
      let root
      act(() => { root = create(jsx(component, props)) })
      roots.push(root)
      return root
    },
    cleanup() {
      act(() => roots.forEach(root => root.unmount()))
      global.setTimeout = old.setTimeout
      global.clearTimeout = old.clearTimeout
      global.window = old.window
      global.document = old.document
    }
  }
}

const categorizedBoard = {
  version: 7,
  revision: 3,
  tasks: [{
    id: 'task-1',
    title: 'Ship the notes',
    status: 'open',
    plan: 'today',
    category: 'today',
    estimate: 25,
    position: 1024,
    subtasks: [{ id: 'sub-1', title: 'Check the list', done: false }]
  }]
}

function textOf(root) {
  return JSON.stringify(root.toJSON())
}

test('an empty board still paints the Todo pane', () => {
  const env = setup({ version: 7, revision: 0, tasks: [] })
  try {
    const root = env.mount(env.TodoPane, { ctx: env.ctx })
    const painted = textOf(root)
    assert.match(painted, /Todo/)
    assert.match(painted, /Capture a task to Inbox/)
    assert.match(painted, /Shared with Hermes/)
    assert.doesNotMatch(painted, /Ship the notes/)
  } finally {
    env.cleanup()
  }
})

test('a categorized task paints its title and move-later control on the host icon set', () => {
  const env = setup(categorizedBoard)
  try {
    const root = env.mount(env.TodoPane, { ctx: env.ctx })
    const painted = textOf(root)
    assert.match(painted, /Ship the notes/)
    assert.match(painted, /Move task later/)
    const subs = env.mount(env.SubtaskEditor, {
      task: categorizedBoard.tasks[0],
      reorderSubtask() {}
    })
    assert.match(textOf(subs), /Move subtask Check the list later/)
  } finally {
    env.cleanup()
  }
})
