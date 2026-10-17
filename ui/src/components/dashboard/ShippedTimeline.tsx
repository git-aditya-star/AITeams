import { useLayoutEffect, useRef, useState } from 'react'
import type { Shipped } from '../../api'
import { Tip } from '../ui/tooltip'

const DAYS = 14
const DOT = 10 // px
const GAP = 8 // dots closer than this stack upward

// F-18: what shipped in the last 14 days, as dots on a time axis (one per feature at its done time), with the
// count as a big number. Hover a dot for its summary; a click opens the conversation.
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

  const timeline = shipped.timeline ?? []
  const now = Date.now() / 1000
  const from = now - DAYS * 86400
  const x = (t: number) => Math.min(1, Math.max(0, (t - from) / (now - from))) * (width - DOT)
  // Oldest first, each dot on the lowest level whose last dot is far enough to the left.
  const ends: number[] = []
  const dots = [...timeline]
    .sort((a, b) => a.done_at - b.done_at)
    .map((f) => {
      const left = x(f.done_at)
      let level = ends.findIndex((e) => left - e >= GAP)
      if (level < 0) level = ends.length
      ends[level] = left
      return { f, left, level }
    })
  const height = Math.max(1, ends.length) * (DOT + 2) + 10
  const n = timeline.length

  return (
    <section aria-label="Shipped, last 14 days" data-ship className="flex max-w-3xl items-end gap-4">
      <Tip content={n ? `${n} in ${DAYS} days · total ${shipped.total}` : `nothing shipped in ${DAYS} days · total ${shipped.total}`}>
        <span data-ship-count className={`w-12 shrink-0 text-right text-title ${n ? 'text-gray-900' : 'text-gray-300'}`}>
          {n}
        </span>
      </Tip>
      <div className="min-w-0 flex-1">
        <div ref={axis} className="relative" style={{ height }}>
          {/* day ticks on the baseline */}
          <div className="absolute inset-x-0 bottom-0 h-px bg-gray-300" />
          {Array.from({ length: DAYS + 1 }, (_, d) => (
            <span key={d} className="absolute bottom-0 h-1.5 w-px bg-gray-300" style={{ left: (d / DAYS) * (width - 1) }} />
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
                style={{ left, bottom: 4 + level * (DOT + 2), width: DOT, height: DOT }}
              />
            </Tip>
          ))}
        </div>
        <div className="mt-0.5 flex justify-between text-caption text-gray-400">
          <span>{DAYS}d</span>
          <span>today</span>
        </div>
      </div>
    </section>
  )
}
