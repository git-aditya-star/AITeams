import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Message } from '../api'
import { roleName } from '../roles'
import MessageRow from './MessageRow'

type Chunk = { kind: 'single'; m: Message } | { kind: 'run'; key: number; items: Message[] }

// Runs of ≥3 consecutive role messages collapse; the last run in the list never does.
// Messages carrying a card (`ref`) break runs so the card is never hidden.
function chunk(messages: Message[]): Chunk[] {
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
    } else {
      flush(false)
      out.push({ kind: 'single', m })
    }
  })
  return out
}

export default function MessageList({
  messages,
  hasMore,
  onLoadMore,
  footer,
}: {
  messages: Message[]
  hasMore: boolean
  onLoadMore: () => void
  footer?: React.ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const nearBottom = useRef(true)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const chunks = useMemo(() => chunk(messages), [messages])

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

  return (
    <div ref={ref} onScroll={onScroll} className="flex-1 overflow-y-auto py-2">
      {hasMore && (
        <div className="text-center py-1">
          <button className="text-xs text-blue-600 hover:underline" onClick={onLoadMore}>
            Load earlier messages
          </button>
        </div>
      )}
      {chunks.map((c) =>
        c.kind === 'single' ? (
          <MessageRow key={c.m.id} m={c.m} />
        ) : expanded.has(c.key) ? (
          <div key={c.key} className="border-l-2 border-gray-200 ml-4">
            {c.items.map((m) => (
              <MessageRow key={m.id} m={m} />
            ))}
            <button
              className="text-xs text-gray-500 hover:text-gray-800 px-4 py-1"
              onClick={() => setExpanded((s) => { const n = new Set(s); n.delete(c.key); return n })}
            >
              ▾ collapse
            </button>
          </div>
        ) : (
          <button
            key={c.key}
            className="w-full text-left flex items-center gap-2 px-4 py-1.5 text-[13px] text-gray-500 hover:bg-gray-50"
            onClick={() => setExpanded((s) => new Set(s).add(c.key))}
          >
            <span className="text-gray-400">▸</span>
            <span className="font-medium text-gray-600">{c.items.length} replies</span>
            <span>· {[...new Set(c.items.map((m) => roleName(m.author)))].join(', ')}</span>
          </button>
        ),
      )}
      {footer}
    </div>
  )
}
