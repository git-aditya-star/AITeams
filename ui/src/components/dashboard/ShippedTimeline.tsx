import { useLayoutEffect, useRef, useState } from 'react'
import type { Shipped } from '../../api'
import { Tip } from '../ui/tooltip'

const DAYS = 14
const DOT = 10 // px
const GAP = 8 // dots closer than this stack upward
const ROWS = 3
const LABEL_EVERY = 3 // a date label every 3 days back from today

export const shippedHeadline = (shipped: Shipped) => `${shipped.timeline?.length ?? 0} shipped · ${shipped.total} total`

const dateLabel = (d: Date) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

// F-18: what shipped in the last 14 days: dots on a time axis (one per feature at its done time) with a tick per
// local day, a date label every 3 days and "today" at the right end; below it the latest 3 as rows (summary on
// hover) and "+N more". A dot or a row opens the conversation.
export default function ShippedTimeline({ shipped, onSelect }: { shipped: Shipped; onSelect: (id: string) => void }) {
  const axis = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(480)
  useLayoutEffect(() => {
    const el = axis.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const timeline = [...(shipped.timeline ?? [])].sort((a, b) => b.done_at - a.done_at)
  const nowMs = Date.now()
  const now = nowMs / 1000
  const from = now - DAYS * 86400
  const pct = (t: number) => ((t - from) / (now - from)) * 100
  const x = (t: number) => Math.min(1, Math.max(0, (t - from) / (now - from))) * (width - DOT)

  // A tick at each local midnight inside the window, k days back from today's; labelled every LABEL_EVERY days.
  const days = Array.from({ length: DAYS }, (_, k) => {
    const d = new Date(nowMs)
    d.setHours(0, 0, 0, 0)
    d.setDate(d.getDate() - k)
    return { k, left: pct(d.getTime() / 1000), label: k > 0 && k % LABEL_EVERY === 0 ? dateLabel(d) : null }
  })

  // Oldest first, each dot on the lowest level whose last dot is far enough to the left.
  const ends: number[] = []
  const dots = [...timeline].reverse().map((f) => {
    const left = x(f.done_at)
    let level = ends.findIndex((e) => left - e >= GAP)
    if (level < 0) level = ends.length
    ends[level] = left
    return { f, left, level }
  })
  const height = Math.max(1, ends.length) * (DOT + 2) + 18
  const rows = timeline.slice(0, ROWS)
  const hidden = timeline.slice(ROWS)

  return (
    <section aria-label="Shipped, last 14 days" data-ship className="flex flex-col gap-3">
      <div>
        <div ref={axis} className="relative" style={{ height }}>
          <div className="absolute inset-x-0 bottom-0 h-px bg-gray-300" />
          {days.map(({ k, left, label }) => (
            <span key={k} className={`absolute bottom-0 w-px bg-gray-300 ${label ? 'h-2' : 'h-1.5'}`} style={{ left: `${left}%` }} />
          ))}
          {dots.map(({ f, left, level }) => (
            <Tip
              key={f.id}
              content={
                <span className="block">
                  <span className="font-mono">{f.key}</span> {f.title}
                  {f.summary && <span className="block text-gray-300">{f.summary}</span>}
                </span>
              }
            >
              <button
                data-ship-dot={f.key}
                aria-label={`${f.key} ${f.title}`}
                onClick={() => onSelect(f.conversation_id)}
                className="absolute cursor-pointer rounded-full bg-emerald-500 ring-2 ring-white hover:bg-emerald-700"
                style={{ left, bottom: 6 + level * (DOT + 2), width: DOT, height: DOT }}
              />
            </Tip>
          ))}
        </div>
        <div data-ship-axis className="relative mt-1 h-4 text-caption text-gray-500">
          {days
            .filter((d) => d.label)
            .reverse() // oldest first, so the DOM order is the visual order
            .map((d) => (
              <span key={d.k} data-ship-tick className="absolute top-0 -translate-x-1/2 whitespace-nowrap" style={{ left: `${d.left}%` }}>
                {d.label}
              </span>
            ))}
          <span data-ship-tick className="absolute top-0 right-0 whitespace-nowrap">
            today
          </span>
        </div>
      </div>

      {timeline.length === 0 ? (
        <div data-ship-empty className="text-meta text-gray-400">
          Nothing shipped in {DAYS} days
        </div>
      ) : (
        <div className="flex flex-col">
          {rows.map((f) => (
            <Tip key={f.id} content={f.summary}>
              <button
                data-ship-row={f.key}
                onClick={() => onSelect(f.conversation_id)}
                className="flex min-w-0 cursor-pointer items-center gap-2 rounded px-1 py-1 text-left hover:bg-gray-50"
              >
                <span className="shrink-0 font-mono text-caption text-gray-500">{f.key}</span>
                <span className="min-w-0 flex-1 truncate text-meta">{f.title}</span>
              </button>
            </Tip>
          ))}
          {hidden.length > 0 && (
            <button
              data-ship-more
              onClick={() => onSelect(hidden[0].conversation_id)}
              className="cursor-pointer self-start px-1 py-1 text-caption text-gray-500 hover:underline"
            >
              +{hidden.length} more
            </button>
          )}
        </div>
      )}
    </section>
  )
}
