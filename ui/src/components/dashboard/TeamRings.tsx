import type { TeamRole } from '../../api'
import { RING_STROKE, ROLES, ringTone, roleName, stateWord } from '../../roles'
import Avatar from '../Avatar'
import RoleActions from '../RoleActions'
import { Tip } from '../ui/tooltip'

const SIZE = 48
const STROKE = 4
const R = (SIZE - STROKE) / 2
const C = 2 * Math.PI * R

const details = (r: string, t: TeamRole | undefined) =>
  t
    ? [
        roleName(r),
        stateWord(t),
        t.model_label || t.model,
        `context ${Math.round((t.context_pct || 0) * 100)}%`,
        `session #${t.session_seq ?? '—'}`,
        `fresh starts ${t.fresh_starts ?? 0}`,
      ].join(' · ')
    : roleName(r)

// F-18: one avatar per role inside a ring: the arc is context used, the colour the state (red when the last
// turn was cut short; the faint track carries it at 0%). The feature it's on sits below. A click opens Stop / Fresh start.
export default function TeamRings({ byRole }: { byRole: Record<string, TeamRole> }) {
  return (
    <section aria-label="Team" className="flex flex-wrap gap-5">
      {ROLES.map((r) => {
        const t = byRole[r]
        const pct = Math.min(1, Math.max(0, t?.context_pct || 0))
        const tone = ringTone(t)
        return (
          <div key={r} className="flex w-14 flex-col items-center gap-1">
            <RoleActions
              role={r}
              trigger={
                <button data-ring={r} data-ring-tone={tone} aria-label={details(r, t)} className="relative cursor-pointer rounded-full">
                  <Tip content={details(r, t)}>
                    <span className="relative block" style={{ width: SIZE, height: SIZE }}>
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
                  </Tip>
                </button>
              }
            />
            <span className="h-4 max-w-full truncate font-mono text-caption text-gray-500" data-ring-feature={r}>
              {t?.current?.feature_key ?? ''}
            </span>
          </div>
        )
      })}
    </section>
  )
}
