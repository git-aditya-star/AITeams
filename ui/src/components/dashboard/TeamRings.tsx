import type { TeamRole } from '../../api'
import { RING_STROKE, ROLES, TONE_TEXT, ringTone, ringWord, roleName } from '../../roles'
import Avatar from '../Avatar'
import RoleActions from '../RoleActions'
import { Tip } from '../ui/tooltip'

const SIZE = 44
const STROKE = 4
const R = (SIZE - STROKE) / 2
const C = 2 * Math.PI * R

// The card headline: how many roles are working (or, when none is, how many wait in the queue).
export function teamHeadline(byRole: Record<string, TeamRole>) {
  const n = (s: string) => ROLES.filter((r) => byRole[r]?.state === s).length
  return n('running') ? `${n('running')} working` : n('queued') ? `${n('queued')} queued` : 'all idle'
}

const pctOf = (t: TeamRole | undefined) => Math.min(1, Math.max(0, t?.context_pct || 0))

const hover = (t: TeamRole | undefined) => (t ? [t.model_label || t.model, `session #${t.session_seq ?? '—'}`, `fresh starts ${t.fresh_starts ?? 0}`].join(' · ') : '')

// F-18: one block per role: the avatar in a ring (arc = context used, colour = state; red when the last turn was
// cut short), the name under it, the state word in the ring colour, and "F-n · 34%" (the feature it is on, or "—",
// then its context). F-22: seven roles, so the grid follows the card's own width (container queries): 4 per row
// (4 + 3) in the usual card, 3 per row (3 + 3 + 1) when it gets very narrow; whole blocks either way.
// Hover shows model, session and fresh starts; a click opens Stop / Fresh start.
export default function TeamRings({ byRole }: { byRole: Record<string, TeamRole> }) {
  return (
    <section aria-label="Team" className="@container">
      <div className="grid grid-cols-3 gap-x-1 gap-y-3 @[18rem]:grid-cols-4">
        {ROLES.map((r) => {
          const t = byRole[r]
          const pct = pctOf(t)
          const tone = ringTone(t)
          return (
            <RoleActions
              key={r}
              role={r}
              trigger={
                <button data-ring={r} data-ring-tone={tone} className="block min-w-0 cursor-pointer rounded-md p-0.5 hover:bg-gray-50">
                  <Tip content={hover(t)}>
                    <span className="flex min-w-0 flex-col items-center gap-1 text-center">
                      <span className="relative block shrink-0" style={{ width: SIZE, height: SIZE }}>
                        <svg width={SIZE} height={SIZE} className="absolute inset-0 -rotate-90">
                          <circle cx={SIZE / 2} cy={SIZE / 2} r={R} fill="none" strokeWidth={STROKE} className={`${RING_STROKE[tone]} opacity-25`} />
                          {pct > 0 && (
                            <circle
                              cx={SIZE / 2}
                              cy={SIZE / 2}
                              r={R}
                              fill="none"
                              strokeWidth={STROKE}
                              strokeLinecap="round"
                              strokeDasharray={`${pct * C} ${C}`}
                              className={`${RING_STROKE[tone]} ${tone === 'green' ? 'animate-pulse' : ''}`}
                            />
                          )}
                        </svg>
                        <span className="absolute inset-0 flex items-center justify-center">
                          <Avatar author={r} size={SIZE - 2 * STROKE - 4} tip={false} />
                        </span>
                      </span>
                      <span className="flex w-full min-w-0 flex-col leading-tight">
                        <span data-ring-name className="truncate text-body font-medium text-gray-900">
                          {roleName(r)}
                        </span>
                        <span data-ring-state className={`truncate text-meta ${TONE_TEXT[tone]}`}>
                          {ringWord(t)}
                        </span>
                        <span data-ring-detail className="truncate text-caption text-gray-500">
                          {t?.current?.feature_key ?? '—'} · {Math.round(pct * 100)}%
                        </span>
                      </span>
                    </span>
                  </Tip>
                </button>
              }
            />
          )
        })}
      </div>
    </section>
  )
}
