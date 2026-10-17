import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Message } from '../api'
import { fmtTime } from '../api'
import { roleName } from '../roles'
import MessageRow from './MessageRow'
import { Chevron, Refresh } from './icons'

type Chunk = { kind: 'single'; m: Message } | { kind: 'run'; key: number; items: Message[] }

// Runs of ≥3 consecutive role messages collapse; the last run in the list never does.
// Messages carrying a card (`ref`) or the fresh-start marker break runs.
function chunk(messages: Message[], resetAfter: number): Chunk[] {
  const out: Chunk[] = []
  let run: Message[] = []
  const flush = (isLast: boolean) => {
    if (run.length >= 3 && !isLast) out.push({ kind: 'run', key: run[0].id, items: run })
    else run.forEach((m) => out.push({ kind: 'single', m }))
    run = []
  }
  messages.forEach((m, i) => {
    if (m.author !== 'master' && !m.ref) {
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

export default function MessageList({
  messages,
  hasMore,
  onLoadMore,
  footer,
  resetAfter = 0,
  highlight,
}: {
  messages: Message[]
  hasMore: boolean
  onLoadMore: () => void
  footer?: React.ReactNode
  resetAfter?: number
  highlight?: number | null
}) {
  const ref = useRef<HTMLDivElement>(null)
  const nearBottom = useRef(true)
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
    if (!highlight) return
    const el = document.getElementById(`m-${highlight}`)
    if (el) { nearBottom.current = false; el.scrollIntoView({ block: 'center' }) }
  }, [highlight, messages.length])

  // The divider sits before the first message newer than the marker (even if the marked one is not loaded).
  let dividerShown = false
  const divider = (c: Chunk) => {
    const first = firstOf(c)
    if (dividerShown || !resetAfter || first.id <= resetAfter) return null
    dividerShown = true
    return (
      <div key={`reset-${first.id}`} className="flex items-center gap-2 px-4 py-2 text-[11.5px] text-gray-500 select-none">
        <span className="flex-1 border-t border-gray-200" />
        <Refresh size={12} className="text-gray-400" />
        <span>Fresh start · {fmtTime(first.created_at)}</span>
        <span className="flex-1 border-t border-gray-200" />
      </div>
    )
  }

  return (
    <div ref={ref} onScroll={onScroll} className="flex-1 overflow-y-auto py-2">
      {hasMore && (
        <div className="text-center py-1">
          <button className="text-xs text-blue-600 hover:underline" onClick={onLoadMore}>
            Load earlier messages
          </button>
        </div>
      )}
      {chunks.map((c) => (
        <div key={c.kind === 'single' ? c.m.id : c.key}>
          {divider(c)}
          {c.kind === 'single' ? (
            <MessageRow m={c.m} highlight={c.m.id === highlight} />
          ) : expanded.has(c.key) ? (
            <div className="border-l-2 border-gray-200 ml-4">
              {c.items.map((m) => (
                <MessageRow key={m.id} m={m} highlight={m.id === highlight} />
              ))}
              <button
                className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-800 px-4 py-1"
                onClick={() => setExpanded((s) => { const n = new Set(s); n.delete(c.key); return n })}
              >
                <Chevron size={12} down /> collapse
              </button>
            </div>
          ) : (
            <button
              className="w-full text-left flex items-center gap-2 px-4 py-1.5 text-[13px] text-gray-500 hover:bg-gray-50"
              onClick={() => setExpanded((s) => new Set(s).add(c.key))}
            >
              <Chevron size={12} className="text-gray-400" />
              <span className="font-medium text-gray-600">{c.items.length} replies</span>
              <span>· {[...new Set(c.items.map((m) => roleName(m.author)))].join(', ')}</span>
            </button>
          )}
        </div>
      ))}
      {footer}
    </div>
  )
}
