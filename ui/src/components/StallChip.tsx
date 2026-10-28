import type { Stall } from '../api'
import { roleName } from '../roles'
import { Badge } from './ui/badge'
import { Tip } from './ui/tooltip'

// F-38: who an open feature is waiting on while the whole team is idle. Steps 1-2 (nudged, then the Manager told) are
// grey: the daemon is still on it. Step 3 (`stuck`) is amber: nothing moved, and there are no more automatic wakes.
// `compact` is for the sidebar row, where the title needs the room: "waiting · Lead 2" / "stuck · Lead 2".
const WAITING = 'bg-gray-100 text-gray-700'
const STUCK = 'bg-amber-100 text-amber-800'

const spanText = (since: number) => {
  const s = Math.max(0, Date.now() / 1000 - since)
  if (s < 60) return 'under a minute'
  if (s < 3600) return `${Math.round(s / 60)} min`
  if (s < 86400) return `${Math.round(s / 3600)} h`
  return `${Math.round(s / 86400)} d`
}

const reason = (s: Stall) => {
  const n = s.ref.split(':')[1]
  if (s.kind === 'review') return `Commit request #${n} waits on its review`
  if (s.kind === 'message') return `Message #${n} asks ${roleName(s.who)} to act, with no reply`
  return `Message #${n} mentions nobody`
}

const progress = (s: Stall) => (s.step >= 3 ? 'nudged, the Manager told, no more automatic wakes' : s.step === 2 ? 'nudged, the Manager told' : 'nudged')

const stallTip = (s: Stall) => `${reason(s)} · waiting ${spanText(s.since)} · ${progress(s)}`

export default function StallChip({ stall, compact = false }: { stall: Stall | null | undefined; compact?: boolean }) {
  if (!stall) return null
  const who = roleName(stall.who)
  const text = compact ? `${stall.stuck ? 'stuck' : 'waiting'} · ${who}` : `${stall.stuck ? 'Stuck · ' : ''}Waiting on ${who}`
  return (
    <Tip content={stallTip(stall)}>
      <span data-stall-chip={stall.stuck ? 'stuck' : 'waiting'} data-stall-who={stall.who} className="inline-flex shrink-0">
        <Badge className={`px-1.5 ${stall.stuck ? `${STUCK} font-semibold` : WAITING}`}>{text}</Badge>
      </span>
    </Tip>
  )
}
