/**
 * Vibeline rails — Ink TUI widget.
 * DATA is injected by vibeline.widget.install_widget. No JSX, no imports.
 */
export default function register(sdk) {
  const { Box, Dialog, Overlay, Text, defineWidgetApp, h, isCtrl, openWidget } = sdk
  const DATA = __VIBELINE_DATA__
  const AUTO_OPEN = __VIBELINE_AUTO_OPEN__

  const GLYPH = { open: '◉', spawn: '◆', note: '•', red: '○', fix: '○', fail: '✕', pass: '●', done: '◉' }
  const glyph = (k) => GLYPH[k] || '•'
  // One hue per meaning, all from the active skin.
  const tone = (k, t) => {
    if (k === 'fail') return t.color.error
    if (k === 'done' || k === 'pass') return t.color.ok
    if (k === 'red' || k === 'fix') return t.color.warn
    if (k === 'spawn') return t.color.shellDollar || t.color.accent
    if (k === 'open') return t.color.primary
    return t.color.label
  }
  // Who keeps the cell (and the label) when events collide; earlier wins.
  const PRIORITY = ['fail', 'done', 'pass', 'open', 'red', 'fix', 'spawn', 'note']
  const rank = (k) => (PRIORITY.indexOf(k) < 0 ? PRIORITY.length : PRIORITY.indexOf(k))
  const cell = (x, width) => Math.max(0, Math.min(width - 1, Math.round(x * (width - 1))))
  const STATUS = { complete: ['✔', 'done'], blocked: ['✕', 'fail'], open: ['◌', 'red'] }
  const LEGEND = [
    ['open', 'open'],
    ['spawn', 'spawn'],
    ['note', 'note'],
    ['red', 'tdd red'],
    ['fail', 'fail'],
    ['pass', 'pass']
  ]

  const lanes = DATA.lanes || []

  function clampState(state) {
    const lane = Math.max(0, Math.min(lanes.length - 1, state.lane || 0))
    const events = (lanes[lane] && lanes[lane].events) || []
    const ev = Math.max(0, Math.min(Math.max(events.length - 1, 0), state.ev || 0))
    return { lane, ev }
  }

  // A clashing label slides off-centre before it is dropped. The selected event always gets its label.
  function placeLabels(events, width, selId) {
    const taken = Array.from({ length: width }, () => false)
    const placed = []
    const order = [...events].sort((a, b) => (b.id === selId) - (a.id === selId) || rank(a.k) - rank(b.k))
    for (const e of order) {
      const n = e.label.length
      const x = cell(e.x, width)
      if (n > width) continue
      if (placed.some((p) => p.e.label === e.label && Math.abs(cell(p.e.x, width) - x) <= n + 1)) continue
      const centre = Math.max(0, Math.min(width - n, x - Math.floor(n / 2)))
      const offs = []
      for (let o = 0; o <= n + 1; o++) offs.push(-o, o)
      for (const off of offs) {
        const start = centre + off
        if (start < 0 || start + n > width) continue
        let clash = false
        for (let i = Math.max(0, start - 1); i < Math.min(width, start + n + 1); i++) clash = clash || taken[i]
        if (clash) continue
        for (let i = start; i < start + n; i++) taken[i] = true
        placed.push({ e, start })
        break
      }
    }
    return placed.sort((a, b) => a.start - b.start)
  }

  function Labels({ events, width, selId, t }) {
    const nodes = []
    let col = 0
    for (const { e, start } of placeLabels(events, width, selId)) {
      if (start > col) nodes.push(h(Text, { key: 'g' + col }, ' '.repeat(start - col)))
      const sel = e.id === selId
      nodes.push(h(Text, { key: 'l' + start, bold: sel, underline: sel, color: tone(e.k, t) }, e.label))
      col = start + e.label.length
    }
    return h(Box, { flexDirection: 'row' }, ...nodes)
  }

  // The rail only exists between the lane's first and last event.
  function Rail({ blocked, events, width, selId, t }) {
    const cells = Array.from({ length: width }, () => null)
    for (const e of events) {
      const x = cell(e.x, width)
      const cur = cells[x]
      if (!cur || e.id === selId || (cur.id !== selId && rank(e.k) < rank(cur.k))) cells[x] = e
    }
    let lo = width
    let hi = -1
    cells.forEach((e, i) => {
      if (e) {
        lo = Math.min(lo, i)
        hi = i
      }
    })
    const lineColor = blocked ? t.color.error : t.color.border
    const nodes = []
    let i = 0
    while (i < width) {
      const e = cells[i]
      if (e) {
        const sel = e.id === selId
        nodes.push(h(Text, { key: String(i), bold: true, inverse: sel, color: tone(e.k, t) }, glyph(e.k)))
        i++
        continue
      }
      let j = i
      while (j < width && !cells[j]) j++
      const on = i > lo && i < hi
      nodes.push(h(Text, { key: String(i), color: lineColor }, (on ? '─' : ' ').repeat(j - i)))
      i = j
    }
    return h(Box, { flexDirection: 'row' }, ...nodes)
  }

  function Body({ state, t, width }) {
    const s = clampState(state)
    const lane = lanes[s.lane] || { id: '—', events: [], rail: [], blocked: false }
    const ev = (lane.events || [])[s.ev] || { label: '—', clock: '', why: '', k: 'note', id: '' }
    const nameW = 10
    const railW = Math.max(12, width - nameW - 1)
    const selId = ev.id
    const acs = DATA.ac || []
    const [mark, statusTone] = STATUS[DATA.status] || ['·', 'note']
    const [startClock, endClock] = String(DATA.clock || '').split(' → ')

    const laneRows = lanes.map((ln, idx) => {
      const active = idx === s.lane
      const tag = (ln.blocked ? ln.id + '!' : ln.id).slice(0, nameW - 2)
      const name = ((active ? '▸ ' : '  ') + tag).padEnd(nameW)
      const nameColor = ln.blocked ? t.color.error : active ? t.color.primary : t.color.label
      return h(
        Box,
        { flexDirection: 'column', key: ln.id },
        h(
          Box,
          { flexDirection: 'row' },
          h(Text, { bold: active, color: nameColor }, name + ' '),
          h(Labels, { events: ln.rail || [], selId, t, width: railW })
        ),
        h(
          Box,
          { flexDirection: 'row' },
          h(Text, null, ''.padEnd(nameW) + ' '),
          h(Rail, { blocked: ln.blocked, events: ln.rail || [], selId, t, width: railW })
        )
      )
    })

    return h(
      Box,
      { flexDirection: 'column' },
      h(
        Box,
        { flexDirection: 'row' },
        h(Text, { bold: true, color: tone(statusTone, t) }, `${mark} ${DATA.status}`),
        h(Text, { color: t.color.muted }, `  ${DATA.dur}`)
      ),
      h(
        Box,
        { flexDirection: 'row', marginBottom: 1 },
        ...(acs.length
          ? [
              h(Text, { bold: true, color: t.color.text, key: 'n' }, `AC ${acs.filter((a) => a.met).length}/${acs.length}`),
              ...acs.map((a) =>
                h(Text, { color: a.met ? t.color.ok : t.color.muted, key: a.id }, `   ${a.met ? '✔' : '○'} ${a.id}`)
              )
            ]
          : [h(Text, { color: t.color.muted, key: 'n' }, 'no AC')])
      ),
      ...laneRows,
      h(
        Text,
        { color: t.color.muted },
        ''.padEnd(nameW + 1) + (startClock || '') + (endClock || '').padStart(railW - (startClock || '').length)
      ),
      h(
        Box,
        { flexDirection: 'row', marginTop: 1 },
        h(Text, { bold: true, inverse: true, color: tone(ev.k, t) }, ` ${ev.label} `),
        h(Text, { bold: true, color: t.color.text }, `  ${lane.id}`),
        h(Text, { color: t.color.muted }, `  ${ev.clock}`)
      ),
      h(Text, { color: t.color.text }, ev.why || ' '),
      h(
        Box,
        { flexDirection: 'row', marginTop: 1 },
        ...LEGEND.flatMap(([k, text], i) => [
          h(Text, { color: tone(k, t), key: k }, (i ? '  ' : '') + glyph(k)),
          h(Text, { color: t.color.muted, key: k + 't' }, ' ' + text)
        ])
      )
    )
  }

  const app = defineWidgetApp({
    id: 'rails',
    help: 'agent-harness rails (parent/child, spawn-start, FAIL stays)',
    mode: 'modal',
    usage: 'usage: /rails',
    init() {
      return { lane: 0, ev: 0 }
    },
    reduce(state, { ch, key }) {
      if (key.escape || ch === 'q' || isCtrl(key, ch, 'c')) return null
      const s = clampState(state)
      const lane = lanes[s.lane] || { events: [] }
      const nEv = (lane.events || []).length
      if (key.leftArrow || ch === 'h') return { lane: s.lane, ev: Math.max(0, s.ev - 1) }
      if (key.rightArrow || ch === 'l') return { lane: s.lane, ev: Math.min(Math.max(nEv - 1, 0), s.ev + 1) }
      if (key.upArrow || ch === 'k') {
        const laneI = Math.max(0, s.lane - 1)
        const maxEv = Math.max(((lanes[laneI] && lanes[laneI].events) || []).length - 1, 0)
        return { lane: laneI, ev: Math.min(s.ev, maxEv) }
      }
      if (key.downArrow || ch === 'j') {
        const laneI = Math.min(lanes.length - 1, s.lane + 1)
        const maxEv = Math.max(((lanes[laneI] && lanes[laneI].events) || []).length - 1, 0)
        return { lane: laneI, ev: Math.min(s.ev, maxEv) }
      }
      return s
    },
    render({ cols, state, t }) {
      const w = Math.min(Math.max(64, cols - 6), 108)
      return h(
        Overlay,
        { backdrop: true, zone: 'center' },
        h(
          Dialog,
          { hint: '← → event   ↑ ↓ lane   q close', title: DATA.task_id || 'rails', width: w },
          h(Body, { state, t, width: Math.max(24, w - 6) })
        )
      )
    }
  })

  if (AUTO_OPEN) {
    openWidget(app, app.init(''))
  }
}
