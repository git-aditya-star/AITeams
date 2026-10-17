import { useEffect, useRef, useState } from 'react'
import type { Turn, TurnEvent } from '../api'
import { api, fmtDuration, fmtTokens } from '../api'
import type { WsFrame } from '../ws'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Toggle } from './ui/toggle'

type Block =
  | { kind: 'text'; id: number; text: string; live: boolean }
  | { kind: 'thinking'; id: number; text: string; live: boolean }
  | { kind: 'tool_use'; id: number; name: string; input: unknown }
  | { kind: 'tool_result'; id: number; text: string; denied: boolean }
  | { kind: 'line'; id: number; text: string; denied?: boolean }
  | { kind: 'result'; id: number; subtype: string; cost: number; turns: number; ms: number; error: string | null }

let nextId = 1
const DENY = 'AITEAMS_DENY'

function contentText(c: unknown): string {
  if (typeof c === 'string') return c
  if (Array.isArray(c)) return c.map((x) => (typeof x === 'string' ? x : x?.text ?? '')).join('')
  return c == null ? '' : JSON.stringify(c)
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
        else if (c.type === 'tool_use') out.push({ kind: 'tool_use', id: nextId++, name: c.name, input: c.input })
      }
      return out
    }
    case 'user': {
      const content = d.message?.content
      if (!Array.isArray(content)) return blocks
      for (const c of content) {
        if (c.type !== 'tool_result') continue
        const text = contentText(c.content)
        out.push({ kind: 'tool_result', id: nextId++, text, denied: text.includes(DENY) })
      }
      return out
    }
    case 'system': {
      if (d.subtype === 'init') out.push({ kind: 'line', id: nextId++, text: `session ${d.session_id} · model ${d.model}` })
      else out.push({ kind: 'line', id: nextId++, text: d.subtype || 'system' })
      return out
    }
    case 'hook_event':
    case 'hook_response': {
      const s = JSON.stringify(d)
      const label = d.hook_event_name || d.hook_name || d.subtype || ''
      out.push({ kind: 'line', id: nextId++, text: `${d.type}${label ? ` · ${label}` : ''}`, denied: s.includes(DENY) })
      return out
    }
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

function Expandable({ text, limit, className }: { text: string; limit: number; className: string }) {
  const [open, setOpen] = useState(false)
  const long = text.length > limit
  return (
    <div className={className}>
      <pre className="whitespace-pre-wrap wrap-break-word font-mono text-code">{open || !long ? text : text.slice(0, limit) + '…'}</pre>
      {long && (
        <Button variant="link" size="inline" className="text-caption font-normal" onClick={() => setOpen((o) => !o)}>
          {open ? 'collapse' : 'expand'}
        </Button>
      )}
    </div>
  )
}

function ToolUse({ name, input }: { name: string; input: unknown }) {
  const [open, setOpen] = useState(false)
  const compact = JSON.stringify(input ?? {})
  return (
    <div className="px-3 py-1 text-meta">
      <span className="font-mono font-semibold text-violet-700">{name}</span>{' '}
      {open ? (
        <pre className="mt-1 whitespace-pre-wrap wrap-break-word font-mono text-code bg-gray-50 rounded-sm p-2">{JSON.stringify(input ?? {}, null, 2)}</pre>
      ) : (
        <span className="font-mono text-gray-500 break-all">{compact.slice(0, 200)}{compact.length > 200 ? '…' : ''}</span>
      )}
      {compact.length > 200 && (
        <Button variant="link" size="inline" className="ml-1 text-caption font-normal" onClick={() => setOpen((o) => !o)}>
          {open ? 'collapse' : 'expand'}
        </Button>
      )}
    </div>
  )
}

function BlockView({ b }: { b: Block }) {
  switch (b.kind) {
    case 'text':
      return <pre className="px-3 py-1 whitespace-pre-wrap wrap-break-word font-sans text-body">{b.text}</pre>
    case 'thinking':
      return <pre className="px-3 py-1 whitespace-pre-wrap wrap-break-word font-sans text-caption text-gray-400 italic">{b.text}</pre>
    case 'tool_use':
      return <ToolUse name={b.name} input={b.input} />
    case 'tool_result':
      return (
        <div className={`mx-3 my-1 ${b.denied ? 'border-l-2 border-red-500 pl-2' : ''}`}>
          {b.denied && <span className="text-caption font-medium text-red-600">hook denied</span>}
          <Expandable text={b.text} limit={300} className="text-gray-500" />
        </div>
      )
    case 'line':
      return <div className={`px-3 py-0.5 text-caption ${b.denied ? 'text-red-600 border-l-2 border-red-500' : 'text-gray-400'}`}>{b.text}</div>
    case 'result':
      return (
        <div className="px-3 py-1.5 text-caption text-gray-600 border-t border-gray-100 mt-1">
          <span className="font-medium">{b.subtype}</span> · {b.turns} turns · {fmtDuration(b.ms / 1000)}
          {b.error && <div className="text-red-600 mt-0.5 whitespace-pre-wrap">{b.error}</div>}
        </div>
      )
  }
}

export default function LiveSession({
  pid,
  role,
  subscribe,
}: {
  pid: string
  role: string
  subscribe: (fn: (f: WsFrame) => void) => () => void
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [blocks, setBlocks] = useState<Block[]>([])
  const [now, setNow] = useState(Date.now() / 1000)
  const [copied, setCopied] = useState(false)
  const [showPrompt, setShowPrompt] = useState(false)
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
      if (ts.length) setSelected(ts[0].id)
    })
  }, [pid, role])

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
          if (follow.current) setSelected((s) => (s == null || t.id > s ? t.id : s))
        } else if (f.event === 'turn_event') {
          const ev = f.data as TurnEvent
          if (ev.turn_id !== selected) return
          if (loading.current) { buffer.current.push(ev); return }
          if (ev.seq <= lastSeq.current) return
          lastSeq.current = ev.seq
          setBlocks((b) => apply(b, ev))
        }
      }),
    [subscribe, role, selected],
  )

  const sel = turns.find((t) => t.id === selected) || null
  const running = turns.some((t) => t.state === 'running')
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
    <div className="flex-1 flex min-h-0" data-live-session>
      <div className="w-[240px] shrink-0 border-r border-gray-200 overflow-y-auto">
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
          {sel?.prompt && (
            <Toggle variant="outline" size="sm" pressed={showPrompt} onPressedChange={setShowPrompt} className="ml-auto data-[state=on]:border-blue-300 data-[state=on]:bg-blue-50 data-[state=on]:text-blue-700">
              Prompt
            </Toggle>
          )}
        </div>
        {showPrompt && sel?.prompt && (
          <pre className="shrink-0 max-h-[40%] overflow-y-auto border-b border-gray-200 bg-gray-50 px-3 py-2 whitespace-pre-wrap wrap-break-word font-mono text-code text-gray-700">{sel.prompt}</pre>
        )}
        <div ref={listRef} className="flex-1 overflow-y-auto py-1">
          {sel?.error && <div className="mx-3 my-1 text-caption text-red-600 whitespace-pre-wrap">{sel.error}</div>}
          {blocks.map((b) => (
            <BlockView key={b.id} b={b} />
          ))}
          {sel && blocks.length === 0 && <div className="p-3 text-caption text-gray-400">no events yet</div>}
        </div>
      </div>
    </div>
  )
}
