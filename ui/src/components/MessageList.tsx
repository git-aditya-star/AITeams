import { ChevronDown, ChevronRight, RotateCw } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Message } from '../api'
import { fmtTime } from '../api'
import { roleName } from '../roles'
import MessageRow, { forMaster } from './MessageRow'
import { Button } from './ui/button'

type Chunk = { kind: 'single'; m: Message } | { kind: 'run'; key: number; items: Message[] }

// Runs of ≥3 consecutive role messages collapse; the last run in the list never does.
// Messages carrying a card (`ref`), messages that tag the Master (F-32: a fold would hide what the tint is there to show)
// and the fresh-start marker break runs.
function chunk(messages: Message[], resetAfter: number): Chunk[] {
  const out: Chunk[] = []
  let run: Message[] = []
  const flush = (isLast: boolean) => {
    if (run.length >= 3 && !isLast) out.push({ kind: 'run', key: run[0].id, items: run })
    else run.forEach((m) => out.push({ kind: 'single', m }))
    run = []
  }
  messages.forEach((m, i) => {
    if (m.author !== 'master' && !m.ref && !forMaster(m)) {
      run.push(m)
      if (i === messages.length - 1) flush(true)
      else if (m.id === resetAfter) flush(false)
    } else {
      flush(false)
      out.push({ kind: 'single', m })
    }
  })
  return out
}

const firstOf = (c: Chunk) => (c.kind === 'single' ? c.m : c.items[0])

const FLASH_MS = 4000 // F-47: how long a jumped-to message stays amber
const SETTLE_MS = 2000 // F-47: how long after a jump the row is re-centred when content above it renders late (a diagram, an image)
const USER_INPUT = ['wheel', 'touchstart', 'pointerdown', 'keydown'] as const // the first of these on the list ends that window

export default function MessageList({
  messages,
  hasMore,
  onLoadMore,
  footer,
  resetAfter = 0,
  highlight,
  onJumped,
}: {
  messages: Message[]
  hasMore: boolean
  onLoadMore: () => void
  footer?: React.ReactNode
  resetAfter?: number
  highlight?: number | null
  onJumped?: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inner = useRef<HTMLDivElement>(null) // F-47: wraps the rows, so a resize observer sees the content grow
  const nearBottom = useRef(true)
  // F-47: `pending` is the jump (`highlight`) not yet done; `flash` is the amber tint after it (a new object per jump, so a repeat restarts the timer).
  const pending = useRef<number | null>(null)
  const [flash, setFlash] = useState<{ id: number } | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const chunks = useMemo(() => chunk(messages, resetAfter), [messages, resetAfter])

  const onScroll = () => {
    const el = ref.current
    if (el) nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }
  useLayoutEffect(() => {
    const el = ref.current
    if (el && nearBottom.current) el.scrollTop = el.scrollHeight
  }, [messages, footer])
  useEffect(() => {
    nearBottom.current = true
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages[0]?.conversation_id])
  useEffect(() => {
    pending.current = highlight || null
  }, [highlight])
  // Scrolls once per jump: new messages find `pending` empty. If the message is not loaded yet, waits for the next `messages`.
  useEffect(() => {
    if (!highlight || pending.current !== highlight) return
    const el = document.getElementById(`m-${highlight}`)
    if (!el) return
    pending.current = null
    nearBottom.current = false
    el.scrollIntoView({ block: 'center' })
    setFlash({ id: highlight })
    onJumped?.()
  }, [highlight, messages, onJumped])
  useEffect(() => {
    if (!flash) return
    const t = setTimeout(() => setFlash(null), FLASH_MS)
    return () => clearTimeout(t)
  }, [flash])
  // Settle window: late-rendering content grows the list above the row after the one scroll, so re-centre on each resize, until the
  // window runs out, the user touches the list, or a new jump starts. New messages never re-scroll outside it.
  useEffect(() => {
    const scroller = ref.current
    const rows = inner.current
    if (!flash || !scroller || !rows) return
    const recentre = () => document.getElementById(`m-${flash.id}`)?.scrollIntoView({ block: 'center' })
    const observer = new ResizeObserver(recentre)
    observer.observe(rows)
    const end = () => {
      observer.disconnect()
      clearTimeout(timer)
      USER_INPUT.forEach((e) => scroller.removeEventListener(e, end))
    }
    const timer = setTimeout(end, SETTLE_MS)
    USER_INPUT.forEach((e) => scroller.addEventListener(e, end, { passive: true }))
    return end
  }, [flash])

  // The divider sits before the first message newer than the marker (even if the marked one is not loaded).
  let dividerShown = false
  const divider = (c: Chunk) => {
    const first = firstOf(c)
    if (dividerShown || !resetAfter || first.id <= resetAfter) return null
    dividerShown = true
    return (
      <div key={`reset-${first.id}`} className="flex items-center gap-2 px-4 py-2 text-caption text-gray-500 select-none">
        <span className="flex-1 border-t border-gray-200" />
        <RotateCw size={12} className="text-gray-400" />
        <span>Fresh start · {fmtTime(first.created_at)}</span>
        <span className="flex-1 border-t border-gray-200" />
      </div>
    )
  }

  return (
    <div ref={ref} onScroll={onScroll} className="flex-1 overflow-y-auto py-2">
      <div ref={inner}>
        {hasMore && (
          <div className="text-center py-1">
            <Button variant="link" size="inline" className="text-caption" onClick={onLoadMore}>
              Load earlier messages
            </Button>
          </div>
        )}
        {chunks.map((c) => (
          <div key={c.kind === 'single' ? c.m.id : c.key}>
            {divider(c)}
            {c.kind === 'single' ? (
              <MessageRow m={c.m} highlight={c.m.id === flash?.id} />
            ) : expanded.has(c.key) ? (
              <div className="border-l-2 border-gray-200 ml-4">
                {c.items.map((m) => (
                  <MessageRow key={m.id} m={m} highlight={m.id === flash?.id} />
                ))}
                <Button
                  variant="ghost"
                  size="xs"
                  className="mx-3 my-0.5 font-normal"
                  onClick={() => setExpanded((s) => { const n = new Set(s); n.delete(c.key); return n })}
                >
                  <ChevronDown /> collapse
                </Button>
              </div>
            ) : (
              <button
                className="w-full cursor-pointer text-left flex items-center gap-2 px-4 py-1.5 text-meta text-gray-500 hover:bg-gray-50"
                onClick={() => setExpanded((s) => new Set(s).add(c.key))}
              >
                <ChevronRight size={12} className="text-gray-400" />
                <span className="font-medium text-gray-600">{c.items.length} replies</span>
                <span>· {[...new Set(c.items.map((m) => roleName(m.author)))].join(', ')}</span>
              </button>
            )}
          </div>
        ))}
        {footer}
      </div>
    </div>
  )
}
