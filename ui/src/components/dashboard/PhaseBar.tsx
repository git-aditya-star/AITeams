import type { DashboardFeature } from '../../api'
import { fmtAgo } from '../../api'
import { PHASES, PHASE_FILL, roleName } from '../../roles'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '../ui/hover-card'
import { Tip } from '../ui/tooltip'

// A feature needs the Master's eye when it waits on them or has gone stale.
const attention = (f: DashboardFeature) =>
  f.stale ? 'stale' : f.waits_on?.who === 'master' ? `waits on you: ${f.waits_on.kind}${f.waits_on.since ? ` since ${fmtAgo(f.waits_on.since)}` : ''}` : null

// The ones needing attention first, then the most recently active.
const sortFeatures = (fs: DashboardFeature[]) =>
  [...fs].sort((a, b) => {
    const d = (attention(b) ? 1 : 0) - (attention(a) ? 1 : 0)
    return d || (b.last_activity ?? b.updated_at) - (a.last_activity ?? a.updated_at)
  })

// F-18: active features as one bar split by phase (width = share, count inside). Hovering a segment lists its
// features; a red dot marks the ones that need the Master; a click opens the conversation.
export default function PhaseBar({ features, onSelect }: { features: DashboardFeature[]; onSelect: (id: string) => void }) {
  const segs = PHASES.filter((p) => p !== 'done')
    .map((p) => ({ phase: p, items: sortFeatures(features.filter((f) => f.phase === p)) }))
    .filter((s) => s.items.length)
  return (
    <section aria-label="Active features by phase" data-phase-bar className="max-w-3xl">
      {segs.length === 0 ? (
        <Tip content="no active features">
          <div data-phase-empty className="h-7 rounded-md bg-gray-100" />
        </Tip>
      ) : (
        <div className="flex h-7 gap-0.5 overflow-hidden rounded-md">
          {segs.map(({ phase, items }) => (
            <HoverCard key={phase}>
              <HoverCardTrigger asChild>
                <button
                  data-phase-seg={phase}
                  aria-label={`${items.length} ${phase}`}
                  className={`relative flex min-w-7 cursor-default items-center justify-center text-caption font-semibold text-white ${PHASE_FILL[phase]}`}
                  style={{ flexGrow: items.length, flexBasis: 0 }}
                >
                  {items.length}
                  {items.some(attention) && <span className="absolute top-1 right-1 size-1.5 rounded-full bg-red-500 ring-1 ring-white" />}
                </button>
              </HoverCardTrigger>
              <HoverCardContent align="start" className="w-80 p-1.5" data-phase-list={phase}>
                <div className="px-1.5 pb-1 text-caption text-gray-500">
                  {phase} · {items.length}
                </div>
                {items.map((f) => {
                  const why = attention(f)
                  return (
                    <button
                      key={f.id}
                      data-phase-row={f.key}
                      onClick={() => onSelect(f.conversation_id)}
                      className="flex w-full cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-gray-50"
                    >
                      <span className={`size-2 shrink-0 rounded-full ${why ? 'bg-red-500' : 'bg-transparent'}`} data-attention={why ? '' : undefined} />
                      <span className="font-mono text-caption text-gray-500 shrink-0">{f.key}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">{f.title}</span>
                        <span className="block truncate text-caption text-gray-500">
                          {why ?? (f.owner ? roleName(f.owner) : '')}
                        </span>
                      </span>
                    </button>
                  )
                })}
              </HoverCardContent>
            </HoverCard>
          ))}
        </div>
      )}
    </section>
  )
}
