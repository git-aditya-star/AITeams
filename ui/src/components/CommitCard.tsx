import type { Commit } from '../api'
import { fmtAgo } from '../api'
import { roleName, stateChip, stateLabel } from '../roles'
import { useAppSelector } from '../store'
import { selectFeatureLabel } from '../store/selectors'
import { useCards } from './CardContext'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Textarea } from './ui/textarea'
import { useCommitDecision } from './useCommitDecision'

type Decision = ReturnType<typeof useCommitDecision>

// Editable commit message, or the plain message once the request is no longer reviewable.
export function CommitMessage({ c, d }: { c: Commit; d: Decision }) {
  return c.state === 'master_review' ? (
    <Textarea
      value={d.message}
      onChange={(e) => d.setMessage(e.target.value)}
      rows={Math.min(8, Math.max(2, d.message.split('\n').length))}
      className="mt-2 resize-y font-mono text-code"
    />
  ) : (
    <pre className="mt-2 whitespace-pre-wrap font-mono text-code text-gray-700 bg-gray-50 rounded-md border border-gray-100 px-2 py-1">{c.message}</pre>
  )
}

// State lines, stale warning, note field and Approve / Commit anyway / Reject.
export function CommitActions({ c, d, showStale = true }: { c: Commit; d: Decision; showStale?: boolean }) {
  const reviewable = c.state === 'master_review'
  return (
    <>
      {showStale && d.stale.length > 0 && reviewable && (
        <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 text-amber-900 px-2 py-1.5 text-caption">
          Files changed since the request: {d.stale.join(', ')}. Review the diff again before committing.
        </div>
      )}

      {c.state === 'lead_review' && <div className="mt-2 text-caption text-gray-500">Waiting on Lead review.</div>}
      {(c.state === 'rejected' || c.state === 'failed') && c.note && (
        <pre className="mt-2 whitespace-pre-wrap font-mono text-code text-red-700 bg-red-50 rounded-md border border-red-100 px-2 py-1">{c.note}</pre>
      )}
      {c.state === 'committed' && c.sha && (
        <div className="mt-2 text-caption text-gray-600">
          Committed as <code className="font-mono text-code bg-gray-100 rounded-sm px-1">{c.sha}</code>
        </div>
      )}

      {reviewable && d.canDecide && (
        <div className="mt-2 flex flex-col gap-1.5">
          <Input value={d.note} onChange={(e) => d.setNote(e.target.value)} placeholder="Note (optional; shown to the requester on reject)" />
          <div className="flex items-center gap-2 flex-wrap">
            {d.stale.length > 0 ? (
              <Button size="sm" disabled={d.busy} onClick={() => d.decide('approve', true)} className="bg-amber-600 hover:bg-amber-700">
                Commit anyway
              </Button>
            ) : (
              <Button size="sm" disabled={d.busy} onClick={() => d.decide('approve')}>
                Approve
              </Button>
            )}
            <Button variant="outline" size="sm" disabled={d.busy} onClick={() => d.decide('reject')}>
              Reject
            </Button>
            {d.err && <span className="text-caption text-red-600 truncate">{d.err}</span>}
          </div>
        </div>
      )}
    </>
  )
}

export default function CommitCard({ c, showFeature = false }: { c: Commit; showFeature?: boolean }) {
  const ctx = useCards()
  const d = useCommitDecision(c)
  const feature = useAppSelector((s) => selectFeatureLabel(s, c.feature_id))
  const files = Array.isArray(c.files) ? c.files : []

  return (
    <div data-card={`commit-${c.id}`} className="mt-1.5 rounded-md border border-gray-200 bg-white px-3 py-2.5 text-meta">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-caption uppercase tracking-wide text-gray-500 font-semibold">Commit #{c.id}</span>
        <Badge className={stateChip(c.state)}>{stateLabel(c.state)}</Badge>
        {c.sha && <code className="font-mono text-code bg-gray-100 rounded-sm px-1">{c.sha.slice(0, 7)}</code>}
        <span className="text-caption text-gray-400">
          {roleName(c.role)}
          {showFeature && feature ? ` · ${feature}` : ''}
          {' · '}
          {files.length} file{files.length === 1 ? '' : 's'} · {fmtAgo(c.created_at)}
        </span>
      </div>

      <ul className="mt-1.5 font-mono text-code text-gray-700">
        {files.map((f) => (
          <li key={f} className={d.stale.includes(f) ? 'text-amber-700' : ''}>
            {f}
            {d.stale.includes(f) && <span className="ml-1 text-caption text-amber-700 font-sans">(changed since request)</span>}
          </li>
        ))}
      </ul>

      <CommitMessage c={c} d={d} />

      {ctx && (
        <div className="mt-1.5">
          <Button variant="link" size="inline" className="text-caption" onClick={() => ctx.onReview(c.id)}>
            Review
          </Button>
        </div>
      )}

      <CommitActions c={c} d={d} />
    </div>
  )
}
