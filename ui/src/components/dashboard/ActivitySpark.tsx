import type { HourBucket } from '../../api'
import { Tip } from '../ui/tooltip'

const hh = (ts: number) => `${String(new Date(ts * 1000).getHours()).padStart(2, '0')}:00`
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`
const COLS = { gridTemplateColumns: 'repeat(24, minmax(0, 1fr))' }

// The card headline: the 24h totals.
export function activityHeadline(hours: HourBucket[]) {
  const sum = (k: 'messages' | 'commits') => hours.reduce((n, h) => n + h[k], 0)
  return `${plural(sum('messages'), 'message')} · ${plural(sum('commits'), 'commit')}`
}

// F-18: the last 24 hours of team activity, one bar per hour (height = messages, nothing drawn for an empty hour),
// a green dot over hours in which a commit landed, an hour axis (a label every 6h, "now" at the right end) and a
// legend. Hovering an hour shows its exact counts.
export default function ActivitySpark({ hours }: { hours: HourBucket[] }) {
  const max = Math.max(0, ...hours.map((h) => h.messages))
  const quiet = hours.every((h) => !h.messages && !h.commits)
  return (
    <section aria-label="Activity, last 24 hours" data-spark className="flex flex-col gap-1">
      {quiet ? (
        <div data-spark-quiet className="flex h-28 items-center justify-center text-meta text-gray-400">
          No activity in 24h
        </div>
      ) : (
        <div className="grid h-28 gap-0.5" style={COLS}>
          {hours.map((h, i) => {
            const label = `${hh(h.start)}–${hh(h.start + 3600)} · ${plural(h.messages, 'message')}${h.commits ? ` · ${plural(h.commits, 'commit')}` : ''}`
            return (
              <Tip key={h.start} content={label}>
                <div data-spark-hour={i} aria-label={label} className="group flex h-full cursor-default flex-col items-center justify-end gap-0.5">
                  {h.commits > 0 && <span data-spark-commit className="size-1.5 shrink-0 rounded-full bg-emerald-500" />}
                  {h.messages > 0 && (
                    <span
                      className="block w-full rounded-t-sm bg-blue-400 group-hover:bg-blue-600"
                      style={{ height: `${Math.max(8, (h.messages / max) * 80)}%` }}
                    />
                  )}
                </div>
              </Tip>
            )
          })}
        </div>
      )}
      {/* the axis: a baseline, a tick + label every 6 local hours, "now" at the right end */}
      <div data-spark-axis className="grid gap-0.5 border-t border-gray-300" style={COLS}>
        {hours.map((h, i) => {
          const last = i === hours.length - 1
          const six = new Date(h.start * 1000).getHours() % 6 === 0 && i < hours.length - 2
          return (
            <div key={h.start} className={`relative h-5 ${last ? 'text-right' : ''}`}>
              {six && (
                <>
                  <span className="absolute top-0 left-0 h-1 w-px bg-gray-300" />
                  <span data-spark-tick className={`absolute top-1 left-0 whitespace-nowrap text-caption text-gray-500 ${i ? '-translate-x-1/2' : ''}`}>
                    {hh(h.start)}
                  </span>
                </>
              )}
              {last && (
                <span data-spark-tick className="absolute top-1 right-0 whitespace-nowrap text-caption text-gray-500">
                  now
                </span>
              )}
            </div>
          )
        })}
      </div>
      <div data-spark-legend className="flex items-center gap-4 text-caption text-gray-500">
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm bg-blue-400" /> messages
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-emerald-500" /> commits
        </span>
      </div>
    </section>
  )
}
