import { useEffect, useMemo, useRef, useState } from 'react'
import type { Turn, TurnEvent } from '../api'
import { api, fmtDuration, fmtTokens } from '../api'
import { DENY, editDiff, expandable, toolBody, toolLine, toolSummary, writeRows, type DiffRow, type ToolResult } from '../lib/terminal'
import { dotClass, laneLabel, roleName, stateWord } from '../roles'
import { useAppSelector } from '../store'
import { selectRole } from '../store/selectors'
import type { WsFrame } from '../ws'
import Markdown from './Markdown'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { Toggle } from './ui/toggle'

// F-33: one block per thing the terminal shows. A tool call is ONE block: its `tool_result` fills it in by `tool_use_id`.
type Block =
  | { kind: 'text'; id: number; text: string; live: boolean }
  | { kind: 'thinking'; id: number; text: string; live: boolean }
  | { kind: 'tool'; id: number; toolId: string; name: string; input: unknown; result?: ToolResult }
  | { kind: 'orphan'; id: number; result: ToolResult } // a result that matches no call
  | { kind: 'line'; id: number; text: string; denied?: boolean; cwd?: string } // system events: only with Show details
  | { kind: 'cmd'; id: number; text: string } // the spawn command: only with Show details
  | { kind: 'result'; id: number; subtype: string; cost: number; turns: number; ms: number; error: string | null }

let nextId = 1

function contentText(c: unknown): string {
  if (typeof c === 'string') return c
  if (Array.isArray(c)) return c.map((x) => (typeof x === 'string' ? x : x?.text ?? '')).join('')
  return c == null ? '' : JSON.stringify(c)
}

// What a hook event reads as in the details: `hook · PreToolUse:Edit · allow`. Claude Code's real events are
// `type:'system'` with subtype hook_started / hook_response (older shapes had their own top-level type).
function hookText(d: Record<string, any>): string {
  const name = d.hook_name || d.hook_event || d.hook_event_name || ''
  const state = d.subtype === 'hook_started' ? 'started' : d.outcome || (d.subtype === 'hook_response' || d.type === 'hook_response' ? 'done' : '')
  return ['hook', name, state].filter(Boolean).join(' · ')
}

// Fold one raw stream-json event into the block list (mutates a copy).
function apply(blocks: Block[], ev: TurnEvent): Block[] {
  const d = ev.data || {}
  const out = blocks.slice()
  const last = out[out.length - 1]
  switch (d.type) {
    case 'stream_event': {
      const e = d.event || {}
      if (e.type !== 'content_block_delta') return blocks
      const dt = e.delta?.type
      if (dt === 'text_delta' || dt === 'thinking_delta') {
        const kind = dt === 'text_delta' ? 'text' : 'thinking'
        const txt = dt === 'text_delta' ? e.delta.text : e.delta.thinking
        if (last && last.kind === kind && last.live) out[out.length - 1] = { ...last, text: last.text + (txt || '') }
        else out.push({ kind, id: nextId++, text: txt || '', live: true })
        return out
      }
      return blocks
    }
    case 'assistant': {
      // Final content replaces whatever streamed in live for this message.
      while (out.length && (out[out.length - 1] as any).live) out.pop()
      for (const c of d.message?.content || []) {
        if (c.type === 'text') out.push({ kind: 'text', id: nextId++, text: c.text || '', live: false })
        else if (c.type === 'thinking') out.push({ kind: 'thinking', id: nextId++, text: c.thinking || '', live: false })
        else if (c.type === 'tool_use') out.push({ kind: 'tool', id: nextId++, toolId: String(c.id ?? ''), name: c.name, input: c.input })
      }
      return out
    }
    case 'user': {
      const content = d.message?.content
      if (!Array.isArray(content)) return blocks
      for (const c of content) {
        if (c.type !== 'tool_result') continue
        const text = contentText(c.content)
        const result: ToolResult = { text, isError: !!c.is_error, denied: text.includes(DENY), data: d.tool_use_result }
        let at = -1
        for (let k = out.length - 1; k >= 0; k--) {
          const b = out[k]
          if (b.kind === 'tool' && b.toolId === String(c.tool_use_id) && !b.result) {
            at = k
            break
          }
        }
        if (at >= 0) out[at] = { ...(out[at] as Extract<Block, { kind: 'tool' }>), result }
        else out.push({ kind: 'orphan', id: nextId++, result })
      }
      return out
    }
    case 'system': {
      if (d.subtype === 'init') out.push({ kind: 'line', id: nextId++, text: `init · session ${d.session_id} · model ${d.model}`, cwd: typeof d.cwd === 'string' ? d.cwd : undefined })
      else if (String(d.subtype || '').startsWith('hook_')) out.push({ kind: 'line', id: nextId++, text: hookText(d), denied: JSON.stringify(d).includes(DENY) })
      else out.push({ kind: 'line', id: nextId++, text: d.subtype || 'system' })
      return out
    }
    case 'aiteams_cmd': // F-9: the command the daemon spawned (long JSON args shown as <json>)
      out.push({ kind: 'cmd', id: nextId++, text: Array.isArray(d.argv) ? d.argv.join(' ') : String(d.argv ?? '') })
      return out
    case 'hook_event':
    case 'hook_response':
      out.push({ kind: 'line', id: nextId++, text: hookText(d), denied: JSON.stringify(d).includes(DENY) })
      return out
    case 'result':
      out.push({
        kind: 'result',
        id: nextId++,
        subtype: d.subtype,
        cost: d.total_cost_usd || 0,
        turns: d.num_turns || 0,
        ms: d.duration_ms || 0,
        error: d.is_error ? contentText(d.result) || d.error || 'error' : null,
      })
      return out
    default:
      return blocks
  }
}

const STATE_BADGE: Record<string, string> = {
  queued: 'bg-gray-100 text-gray-600',
  running: 'bg-green-100 text-green-700 animate-pulse',
  done: 'bg-blue-100 text-blue-700',
  failed: 'bg-red-100 text-red-700',
  interrupted: 'bg-amber-100 text-amber-800',
}

// Thinking, as the terminal shows it (F-33, "Thinking, fixed after the live look"). A headless run returns only the signature,
// so every model step ends with a finished thinking block whose text is EMPTY: showing each one gave a column of identical,
// unexpandable `✻ Thinking…` lines. So, over the blocks that are on screen:
//  - consecutive thinking blocks are one block (their texts joined), so hidden `line`/`cmd` blocks between them don't split it;
//  - a block with no text is not shown, except as the single spinner at the bottom while the turn runs and thinking is
//    the newest thing (the role is thinking right now).
// Done at render time, so `apply()` keeps every event and a later event can still change the answer.
function foldThinking(blocks: Block[], running: boolean): Block[] {
  const out: Block[] = []
  for (const b of blocks) {
    const prev = out[out.length - 1]
    if (b.kind === 'thinking' && prev?.kind === 'thinking') {
      out[out.length - 1] = { ...prev, text: [prev.text, b.text].filter((t) => t.trim()).join('\n\n'), live: prev.live || b.live }
    } else out.push(b)
  }
  return out.filter((b, i) => b.kind !== 'thinking' || b.text.trim() !== '' || (running && i === out.length - 1))
}

// ---------------------------------------------------------------- the terminal pane (dark)

const MAIN = '__main' // Radix Select items can't have an empty value, so the main session's is a sentinel

const CAP = 50_000 // an expanded output longer than this is cut, so a huge Read cannot freeze the page
const capped = (t: string) => (t.length > CAP ? `${t.slice(0, CAP)}\n… (${t.length - CAP} more characters)` : t)
const DOT = { ok: 'text-green-400', error: 'text-red-400', running: 'animate-pulse text-neutral-400', text: 'text-white' } as const
const FOCUS = 'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-neutral-500'

function Dot({ state }: { state: keyof typeof DOT }) {
  return (
    <span data-dot={state} className={`shrink-0 ${DOT[state]}`}>
      ⏺
    </span>
  )
}

// A diff (or the head of a Write): `- 141 │ old`, `+ 141 │ new`, context dim. Long ones show their first `limit` rows.
function Rows({ rows, limit, label }: { rows: DiffRow[]; limit: number; label: string }) {
  const [all, setAll] = useState(false)
  const shown = all ? rows : rows.slice(0, limit)
  const hidden = rows.length - shown.length
  const width = String(Math.max(0, ...rows.map((r) => r.line ?? 0))).length
  return (
    <div data-term-diff={label} className="mt-1 ml-8 overflow-hidden rounded-sm border border-neutral-800">
      {shown.map((r, k) => (
        <div
          key={k}
          data-diff={r.sign === '+' ? 'add' : r.sign === '-' ? 'del' : 'ctx'}
          className={`px-2 whitespace-pre-wrap break-all ${r.sign === '-' ? 'bg-red-950/70 text-red-300' : r.sign === '+' ? 'bg-green-950/70 text-green-300' : 'text-neutral-500'}`}
        >
          <span className="select-none">
            {r.sign} {r.line == null ? ' '.repeat(width) : String(r.line).padStart(width)} │{' '}
          </span>
          {r.text}
        </div>
      ))}
      {hidden > 0 && (
        <button type="button" data-term-more className={`block w-full cursor-pointer px-2 text-left text-neutral-500 hover:bg-white/5 ${FOCUS}`} onClick={() => setAll(true)}>
          … +{hidden} lines (click to expand)
        </button>
      )}
    </div>
  )
}

function ToolView({ b, running, root }: { b: Extract<Block, { kind: 'tool' }>; running: boolean; root: string | null }) {
  const [open, setOpen] = useState(false)
  const r = b.result
  const bad = !!r && (r.isError || r.denied)
  const line = toolLine(b.name, b.input, root)
  const sum = toolSummary(b.name, b.input, r, running, root)
  const body = toolBody(b.name, b.input)
  const can = expandable(b.name, b.input, r)
  const isEdit = /^(Edit|MultiEdit|NotebookEdit)$/.test(b.name)
  const state = !r ? (running ? 'running' : 'ok') : bad ? 'error' : 'ok'
  const head = (
    <div className="flex gap-2">
      <Dot state={!r && !running ? 'text' : state} />
      <div className="min-w-0 break-all">
        <b className="font-semibold text-white">{line.title}</b>
        {line.arg && <>({line.arg})</>}
        {line.dim && (
          <>
            {' '}
            <span className="text-neutral-400">{line.dim}</span>
          </>
        )}
      </div>
    </div>
  )
  const tone = bad ? 'text-red-400' : r ? 'text-neutral-400' : 'text-neutral-500'
  const under = sum && (
    <>
      {sum.text && (
        <div data-term-summary className={`pl-4 ${tone}`}>
          ⎿&nbsp; {sum.text}
          {sum.more && <span className="ml-1 text-neutral-600">{sum.more}</span>}
          {can && !open && <span className="ml-1 text-neutral-600">(click to expand)</span>}
        </div>
      )}
      {sum.list.map((t, k) => (
        <div key={k} className={`pl-8 ${t.startsWith('☒') ? 'text-neutral-500 line-through' : 'text-neutral-300'}`}>
          {t}
        </div>
      ))}
    </>
  )
  return (
    <div data-term-tool={b.name} data-state={bad ? 'error' : r ? 'ok' : running ? 'running' : 'none'} {...(r?.denied ? { 'data-term-denied': '' } : {})}>
      {can ? (
        <button type="button" aria-expanded={open} className={`block w-full cursor-pointer text-left hover:bg-white/5 ${FOCUS}`} onClick={() => setOpen((o) => !o)}>
          {head}
          {under}
        </button>
      ) : (
        <>
          {head}
          {body?.md && (
            <div data-term-body className="mt-1 ml-4 border-l border-neutral-700 pl-3 font-sans text-meta leading-5 text-neutral-200">
              <Markdown text={body.md} />
            </div>
          )}
          {body?.files && (
            <div data-term-body className="pl-8 text-neutral-400">
              {body.files.map((f) => (
                <div key={f}>{f}</div>
              ))}
            </div>
          )}
          {under}
        </>
      )}
      {open && can && r && <pre data-term-output className={`pl-8 whitespace-pre-wrap break-all ${bad ? 'text-red-300' : 'text-neutral-400'}`}>{capped(r.text)}</pre>}
      {r && !bad && isEdit && <Rows rows={editDiff(b.input, r.data)} limit={60} label="edit" />}
      {r && !bad && b.name === 'Write' && <Rows rows={writeRows(b.input)} limit={10} label="write" />}
    </div>
  )
}

function ThinkingView({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  if (!text.trim()) return <div data-term-thinking className="text-neutral-500 italic">✻ Thinking…</div>
  return (
    <div data-term-thinking>
      <button type="button" aria-expanded={open} className={`cursor-pointer text-left text-neutral-500 italic hover:text-neutral-400 ${FOCUS}`} onClick={() => setOpen((o) => !o)}>
        ✻ Thinking…
      </button>
      {open && <div className="mt-1 pl-4 whitespace-pre-wrap text-neutral-500 italic">{text}</div>}
    </div>
  )
}

function BlockView({ b, running, root }: { b: Block; running: boolean; root: string | null }) {
  switch (b.kind) {
    case 'text':
      return (
        <div data-term-text className="flex gap-2">
          <Dot state="text" />
          <div className="min-w-0 flex-1">
            <Markdown text={b.text} />
          </div>
        </div>
      )
    case 'thinking':
      return <ThinkingView text={b.text} />
    case 'tool':
      return <ToolView b={b} running={running} root={root} />
    case 'orphan': {
      const bad = b.result.isError || b.result.denied
      const s = toolSummary('', null, b.result, false)
      return (
        <div data-term-orphan className={`pl-4 ${bad ? 'text-red-400' : 'text-neutral-400'}`}>
          ⎿&nbsp; {s?.text}
        </div>
      )
    }
    case 'cmd':
      return (
        <div className="break-all text-neutral-500" data-aiteams-cmd>
          {b.text}
        </div>
      )
    case 'line':
      return <div className={`break-all ${b.denied ? 'text-red-400' : 'text-neutral-500'}`}>{b.text}</div>
    case 'result':
      return (
        <div data-term-result className="pt-2 text-neutral-500">
          ✻ <span>{b.subtype}</span> · {b.turns} turns · {fmtDuration(b.ms / 1000)}
          {b.error && <div className="mt-0.5 whitespace-pre-wrap text-red-400">{b.error}</div>}
        </div>
      )
  }
}

// F-41: a role has its main session ('') and one session per feature that has its own worktree ('F-36'). This view shows one of them;
// `lane` is the one chosen (a URL param); changing it reloads the turn list and shows that session's newest turn.
export default function LiveSession({
  pid,
  role,
  lane,
  onLane,
  subscribe,
}: {
  pid: string
  role: string
  lane: string
  onLane: (lane: string) => void
  subscribe: (fn: (f: WsFrame) => void) => () => void
}) {
  const team = useAppSelector((s) => selectRole(s, role))
  const [all, setTurns] = useState<Turn[]>([]) // every session's turns; `turns` below is the chosen session's
  const turns = useMemo(() => all.filter((t) => (t.lane || '') === lane), [all, lane])
  const [selected, setSelected] = useState<number | null>(null)
  const [blocks, setBlocks] = useState<Block[]>([])
  const [now, setNow] = useState(Date.now() / 1000)
  const [copied, setCopied] = useState(false)
  const [showPrompt, setShowPrompt] = useState(false)
  const [showDetails, setShowDetails] = useState(false) // F-33: the spawn command, init and hook lines; off on every open
  const follow = useRef(true) // auto-select newest until the user clicks a turn
  const lastSeq = useRef(0)
  const loading = useRef(false)
  const buffer = useRef<TurnEvent[]>([])
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    follow.current = true
    setSelected(null)
    api.turns(pid, role).then((ts) => {
      setTurns(ts)
      const mine = ts.find((t) => (t.lane || '') === lane)
      if (mine) setSelected(mine.id)
    })
  }, [pid, role, lane])

  // Load the selected turn's events; buffer WS events that arrive mid-fetch.
  useEffect(() => {
    if (selected == null) { setBlocks([]); return }
    let cancelled = false
    loading.current = true
    buffer.current = []
    lastSeq.current = 0
    setBlocks([])
    api.turnEvents(pid, selected, 0).then((evs) => {
      if (cancelled) return
      let b: Block[] = []
      for (const ev of [...evs, ...buffer.current]) {
        if (ev.seq <= lastSeq.current) continue
        b = apply(b, ev)
        lastSeq.current = ev.seq
      }
      loading.current = false
      buffer.current = []
      setBlocks(b)
    })
    return () => { cancelled = true }
  }, [pid, selected])

  useEffect(
    () =>
      subscribe((f) => {
        if (f.event === 'turn_state') {
          const t = f.data as Turn
          if (t.role !== role) return
          setTurns((ts) => {
            const i = ts.findIndex((x) => x.id === t.id)
            const next = i >= 0 ? ts.map((x) => (x.id === t.id ? t : x)) : [t, ...ts]
            return next.sort((a, b) => b.id - a.id)
          })
          if (follow.current && (t.lane || '') === lane) setSelected((s) => (s == null || t.id > s ? t.id : s))
        } else if (f.event === 'turn_event') {
          const ev = f.data as TurnEvent
          if (ev.turn_id !== selected) return
          if (loading.current) { buffer.current.push(ev); return }
          if (ev.seq <= lastSeq.current) return
          lastSeq.current = ev.seq
          setBlocks((b) => apply(b, ev))
        }
      }),
    [subscribe, role, lane, selected],
  )

  // The picker lists the main session, every active feature session, and any session whose turns are still on screen (a closed one).
  const sessions = useMemo(() => {
    const keys = new Set<string>(team?.lanes?.map((l) => l.lane))
    for (const t of all) if (t.lane) keys.add(t.lane)
    if (lane) keys.add(lane)
    return [...keys].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
  }, [team?.lanes, all, lane])
  const sessionState = (l: string) => {
    if (!l) return stateWord(team)
    const live = team?.lanes?.find((x) => x.lane === l)
    return live ? stateWord(live) : 'closed'
  }

  const sel = turns.find((t) => t.id === selected) || null
  const running = turns.some((t) => t.state === 'running')
  const live = sel?.state === 'running' // a tool without its result yet is "running" only while its turn is
  const root = useMemo(() => blocks.find((b): b is Extract<Block, { kind: 'line' }> => b.kind === 'line' && !!b.cwd)?.cwd ?? null, [blocks])
  const shown = foldThinking(showDetails ? blocks : blocks.filter((b) => b.kind !== 'line' && b.kind !== 'cmd'), live)
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(id)
  }, [running])

  useEffect(() => {
    const el = listRef.current
    if (el && sel?.state === 'running') el.scrollTop = el.scrollHeight
  }, [blocks, sel?.state])

  const duration = (t: Turn) => {
    if (!t.started_at) return '—'
    return fmtDuration((t.ended_at ?? now) - t.started_at)
  }
  const cmd = sel?.session_id ? `claude --resume ${sel.session_id} --setting-sources local` : null
  const copy = () => {
    if (!cmd) return
    navigator.clipboard?.writeText(cmd).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) })
  }

  return (
    <div className="flex-1 flex min-h-0" data-live-session={lane || 'main'}>
      <div className="w-[240px] shrink-0 border-r border-gray-200 overflow-y-auto">
        {sessions.length > 0 && (
          <div className="sticky top-0 z-10 border-b border-gray-200 bg-white p-2">
            <Select value={lane || MAIN} onValueChange={(v) => onLane(v === MAIN ? '' : v)}>
              <SelectTrigger size="sm" aria-label="Session" data-session-picker className="w-full">
                <SelectValue>{lane ? laneLabel(role, lane) : `${roleName(role)} (main)`}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {['', ...sessions].map((l) => (
                  <SelectItem key={l || MAIN} value={l || MAIN} data-session-option={l || 'main'}>
                    {/* Radix wraps an item's content in an inline span, so the row's own flex goes in here */}
                    <span className="flex items-center gap-2">
                      <span className={`size-2 shrink-0 rounded-full ${dotClass(sessionState(l))}`} />
                      <span>{l ? laneLabel(role, l) : `${roleName(role)} (main)`}</span>
                      <span className="text-caption text-gray-400">{sessionState(l)}</span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        {turns.length === 0 && <div className="p-3 text-caption text-gray-400">no turns yet</div>}
        {turns.map((t) => (
          <button
            key={t.id}
            onClick={() => { follow.current = false; setSelected(t.id) }}
            className={`w-full cursor-pointer text-left px-3 py-2 border-b border-gray-100 text-meta ${t.id === selected ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
          >
            <div className="flex items-center gap-2">
              <span className="font-mono text-gray-500">#{t.id}</span>
              <Badge className={`px-1.5 ${STATE_BADGE[t.state] || STATE_BADGE.queued}`}>{t.state}</Badge>
              {t.kind && t.kind !== 'normal' && <Badge className="px-1.5 bg-violet-50 text-violet-700">{t.kind.replace('_', ' ')}</Badge>}
              <span className="ml-auto text-gray-400">{duration(t)}</span>
            </div>
            <div className="mt-0.5 text-gray-500 flex gap-3">
              <span>{fmtTokens(t.context_tokens || 0)}</span>
              <span className="text-gray-400 truncate">{t.origin}</span>
            </div>
          </button>
        ))}
      </div>
      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-3 py-2 border-b border-gray-200 text-meta flex items-center gap-2 flex-wrap">
          {cmd ? (
            <>
              <code className="font-mono bg-gray-100 rounded-sm px-1.5 py-0.5 text-code truncate" title={cmd}>{cmd}</code>
              <Button variant="outline" size="xs" onClick={copy}>
                {copied ? 'copied' : 'copy'}
              </Button>
              <span className="text-gray-400 text-caption">only while the role is idle</span>
            </>
          ) : (
            <span className="text-gray-400">no session yet</span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Toggle variant="outline" size="sm" pressed={showDetails} onPressedChange={setShowDetails} className="data-[state=on]:border-blue-300 data-[state=on]:bg-blue-50 data-[state=on]:text-blue-700">
              Show details
            </Toggle>
            {sel?.prompt && (
              <Toggle variant="outline" size="sm" pressed={showPrompt} onPressedChange={setShowPrompt} className="data-[state=on]:border-blue-300 data-[state=on]:bg-blue-50 data-[state=on]:text-blue-700">
                Prompt
              </Toggle>
            )}
          </div>
        </div>
        {showPrompt && sel?.prompt && (
          <pre className="shrink-0 max-h-[40%] overflow-y-auto border-b border-gray-200 bg-gray-50 px-3 py-2 whitespace-pre-wrap wrap-break-word font-mono text-code text-gray-700">{sel.prompt}</pre>
        )}
        <div ref={listRef} data-term className="term flex-1 space-y-2 overflow-y-auto bg-neutral-950 px-4 py-3 font-mono text-code text-neutral-200">
          {sel?.error && <div className="whitespace-pre-wrap text-red-400">{sel.error}</div>}
          {shown.map((b) => (
            <BlockView key={b.id} b={b} running={live} root={root} />
          ))}
          {sel && blocks.length === 0 && <div className="text-neutral-500">no events yet</div>}
        </div>
      </div>
    </div>
  )
}
