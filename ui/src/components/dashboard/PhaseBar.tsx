import type { DashboardFeature } from '../../api'
import { fmtAgo } from '../../api'
import { PHASES, PHASE_FILL, phaseChip } from '../../roles'
import { Badge } from '../ui/badge'
import { Tip } from '../ui/tooltip'

const ACTIVE = PHASES.filter((p) => p !== 'done')
const ROWS = 5

const waitsOnYou = (f: DashboardFeature) => f.waits_on?.who === 'master'
const needsYou = (f: DashboardFeature) => waitsOnYou(f) || f.stale

// The ones needing the Master first, then the most recently active.
const sortFeatures = (fs: DashboardFeature[]) =>
  [...fs].sort((a, b) => {
    const d = (needsYou(b) ? 1 : 0) - (needsYou(a) ? 1 : 0)
    return d || (b.last_activity ?? b.updated_at) - (a.last_activity ?? a.updated_at)
  })

export const featuresHeadline = (features: DashboardFeature[]) => `${features.length} active`

// F-18: active features as one bar split by phase (width = share; the count sits inside only when there are
// several segments), a legend with every phase intake → qa and its count (zeros greyed, so it reads as a
// pipeline), then up to 5 feature rows (attention first) with their phase and a "waits on you" / "stale" tag.
// A row opens the conversation.
export default function PhaseBar({ features, onSelect }: { features: DashboardFeature[]; onSelect: (id: string) => void }) {
  const counts = Object.fromEntries(ACTIVE.map((p) => [p, features.filter((f) => f.phase === p).length]))
  const segs = ACTIVE.filter((p) => counts[p])
  const sorted = sortFeatures(features)
  const rows = sorted.slice(0, ROWS)
  const hidden = sorted.slice(ROWS)
  return (
    <section aria-label="Active features by phase" data-phase-bar className="flex flex-col gap-3">
      {segs.length === 0 ? (
        <div data-phase-empty-bar className="h-7 rounded-md bg-gray-100" />
      ) : (
        <div className="flex h-7 gap-0.5 overflow-hidden rounded-md">
          {segs.map((p) => (
            <Tip key={p} content={`${p} · ${counts[p]}`}>
              <div
                data-phase-seg={p}
                aria-label={`${counts[p]} ${p}`}
                className={`flex min-w-7 items-center justify-center text-caption font-semibold text-white ${PHASE_FILL[p]}`}
                style={{ flexGrow: counts[p], flexBasis: 0 }}
              >
                {segs.length > 1 ? counts[p] : null}
              </div>
            </Tip>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-x-2.5 gap-y-1 text-caption">
        {ACTIVE.map((p) => (
          <span key={p} data-phase-legend={p} className={`flex items-center gap-1 ${counts[p] ? 'text-gray-700' : 'text-gray-400'}`}>
            <span className={`size-2.5 rounded-sm ${counts[p] ? PHASE_FILL[p] : 'bg-gray-200'}`} />
            {p} <span className="font-semibold">{counts[p]}</span>
          </span>
        ))}
      </div>

      {features.length === 0 ? (
        <div data-phase-empty className="text-meta text-gray-400">
          No active features
        </div>
      ) : (
        <div className="flex flex-col">
          {rows.map((f) => (
            <button
              key={f.id}
              data-phase-row={f.key}
              onClick={() => onSelect(f.conversation_id)}
              className="flex min-w-0 cursor-pointer items-center gap-2 rounded px-1 py-1 text-left hover:bg-gray-50"
            >
              <span className="shrink-0 font-mono text-caption text-gray-500">{f.key}</span>
              <span className="min-w-0 flex-1 truncate text-meta">{f.title}</span>
              <Badge className={`shrink-0 px-1.5 ${phaseChip(f.phase)}`}>{f.phase}</Badge>
              {waitsOnYou(f) && (
                <Tip content={`waits on you: ${f.waits_on!.kind}${f.waits_on!.since ? ` since ${fmtAgo(f.waits_on!.since)}` : ''}`}>
                  <span data-waits className="shrink-0">
                    <Badge className="bg-red-50 px-1.5 text-red-700">waits on you</Badge>
                  </span>
                </Tip>
              )}
              {f.stale && (
                <Badge data-stale className="shrink-0 bg-gray-100 px-1.5 text-gray-600">
                  stale
                </Badge>
              )}
            </button>
          ))}
          {hidden.length > 0 && (
            <button
              data-phase-more
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
