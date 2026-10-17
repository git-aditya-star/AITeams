import type { Commit } from '../api'
import { fmtAgo } from '../api'
import { roleName, stateChip, stateLabel } from '../roles'
import { featureLabel, useCards } from './CardContext'
import { useCommitDecision } from './useCommitDecision'

const btn = 'rounded-md px-3 py-1 text-[13px] font-medium disabled:opacity-40'

type Decision = ReturnType<typeof useCommitDecision>

// Editable commit message, or the plain message once the request is no longer reviewable.
export function CommitMessage({ c, d }: { c: Commit; d: Decision }) {
  return c.state === 'master_review' ? (
    <textarea
      value={d.message}
      onChange={(e) => d.setMessage(e.target.value)}
      rows={Math.min(8, Math.max(2, d.message.split('\n').length))}
      className="mt-2 w-full resize-y rounded-md border border-gray-300 px-2 py-1 font-mono text-xs focus:outline-none focus:border-blue-400"
    />
  ) : (
    <pre className="mt-2 whitespace-pre-wrap font-mono text-xs text-gray-700 bg-gray-50 rounded-md border border-gray-100 px-2 py-1">{c.message}</pre>
  )
}

// State lines, stale warning, note field and Approve / Commit anyway / Reject.
export function CommitActions({ c, d, showStale = true }: { c: Commit; d: Decision; showStale?: boolean }) {
  const ctx = useCards()
  const reviewable = c.state === 'master_review'
  return (
    <>
      {showStale && d.stale.length > 0 && reviewable && (
        <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 text-amber-900 px-2 py-1.5 text-xs">
          Files changed since the request: {d.stale.join(', ')}. Review the diff again before committing.
        </div>
      )}

      {c.state === 'lead_review' && <div className="mt-2 text-xs text-gray-500">Waiting on Lead review.</div>}
      {(c.state === 'rejected' || c.state === 'failed') && c.note && (
        <pre className="mt-2 whitespace-pre-wrap font-mono text-xs text-red-700 bg-red-50 rounded-md border border-red-100 px-2 py-1">{c.note}</pre>
      )}
      {c.state === 'committed' && c.sha && (
        <div className="mt-2 text-xs text-gray-600">
          Committed as <code className="font-mono bg-gray-100 rounded px-1">{c.sha}</code>
        </div>
      )}

      {reviewable && ctx && (
        <div className="mt-2 flex flex-col gap-1.5">
          <input
            value={d.note}
            onChange={(e) => d.setNote(e.target.value)}
            placeholder="Note (optional; shown to the requester on reject)"
            className="rounded-md border border-gray-300 px-2 py-1 text-[13px] focus:outline-none focus:border-blue-400"
          />
          <div className="flex items-center gap-2 flex-wrap">
            {d.stale.length > 0 ? (
              <button disabled={d.busy} onClick={() => d.decide('approve', true)} className={`${btn} bg-amber-600 text-white hover:bg-amber-700`}>
                Commit anyway
              </button>
            ) : (
              <button disabled={d.busy} onClick={() => d.decide('approve')} className={`${btn} bg-blue-600 text-white hover:bg-blue-700`}>
                Approve
              </button>
            )}
            <button disabled={d.busy} onClick={() => d.decide('reject')} className={`${btn} border border-gray-300 text-gray-700 hover:bg-gray-50`}>
              Reject
            </button>
            {d.err && <span className="text-xs text-red-600 truncate">{d.err}</span>}
          </div>
        </div>
      )}
    </>
  )
}

export default function CommitCard({ c, showFeature = false }: { c: Commit; showFeature?: boolean }) {
  const ctx = useCards()
  const d = useCommitDecision(c)
  const feature = ctx ? featureLabel(ctx.features, c.feature_id) : null
  const files = Array.isArray(c.files) ? c.files : []

  return (
    <div className="mt-1.5 rounded-md border border-gray-200 bg-white px-3 py-2.5 text-[13px]">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold">Commit #{c.id}</span>
        <span className={`text-[11px] rounded-full px-2 py-px font-medium ${stateChip(c.state)}`}>{stateLabel(c.state)}</span>
        {c.sha && <code className="font-mono text-xs bg-gray-100 rounded px-1">{c.sha.slice(0, 7)}</code>}
        <span className="text-xs text-gray-400">
          {roleName(c.role)}
          {showFeature && feature ? ` · ${feature}` : ''}
          {' · '}
          {files.length} file{files.length === 1 ? '' : 's'} · {fmtAgo(c.created_at)}
        </span>
      </div>

      <ul className="mt-1.5 font-mono text-xs text-gray-700">
        {files.map((f) => (
          <li key={f} className={d.stale.includes(f) ? 'text-amber-700' : ''}>
            {f}
            {d.stale.includes(f) && <span className="ml-1 text-[11px] text-amber-700 font-sans">(changed since request)</span>}
          </li>
        ))}
      </ul>

      <CommitMessage c={c} d={d} />

      {ctx && (
        <div className="mt-1.5">
          <button onClick={() => ctx.onReview(c.id)} className="text-xs text-blue-600 hover:underline">
            Review
          </button>
        </div>
      )}

      <CommitActions c={c} d={d} />
    </div>
  )
}
