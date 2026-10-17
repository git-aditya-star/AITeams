import type { HourBucket } from '../../api'
import { Tip } from '../ui/tooltip'

const hh = (ts: number) => `${String(new Date(ts * 1000).getHours()).padStart(2, '0')}:00`
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

// F-18: the last 24 hours of team activity, one bar per hour (height = messages), an emerald dot over hours
// in which a commit landed. Words only in the tooltips.
export default function ActivitySpark({ hours }: { hours: HourBucket[] }) {
  const max = Math.max(0, ...hours.map((h) => h.messages))
  const quiet = hours.every((h) => !h.messages && !h.commits)
  return (
    <section aria-label="Activity, last 24 hours" data-spark className="max-w-3xl">
      {quiet ? (
        <Tip content="quiet for 24h">
          <div data-spark-quiet className="flex h-12 items-end">
            <div className="h-0.5 w-full rounded-sm bg-gray-200" />
          </div>
        </Tip>
      ) : (
        <div className="flex h-12 items-end gap-0.5">
          {hours.map((h, i) => {
            const label = `${hh(h.start)}–${hh(h.start + 3600)} · ${plural(h.messages, 'message')}${h.commits ? ` · ${plural(h.commits, 'commit')}` : ''}`
            return (
              <Tip key={h.start} content={label}>
                <div data-spark-hour={i} aria-label={label} className="group flex h-full flex-1 cursor-default flex-col items-center justify-end gap-0.5">
                  {h.commits > 0 && <span data-spark-commit className="size-1.5 shrink-0 rounded-full bg-emerald-500" />}
                  <span
                    className={`block w-full rounded-sm ${h.messages ? 'bg-blue-400 group-hover:bg-blue-600' : 'bg-gray-200'}`}
                    style={{ height: h.messages ? `${Math.max(8, (h.messages / max) * 80)}%` : 2 }}
                  />
                </div>
              </Tip>
            )
          })}
        </div>
      )}
    </section>
  )
}
