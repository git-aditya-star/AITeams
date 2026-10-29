import type { Commit } from '../api'
import { fmtAgo } from '../api'
import { roleName, stateChip, stateLabel } from '../roles'
import { useAppSelector } from '../store'
import { selectFeatureLabel } from '../store/selectors'
import { useCards } from './CardContext'
import FilePreviews from './FilePreviews'
import { LEAD_TONE, leadReview, sentBackNote } from './leadReview'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Textarea } from './ui/textarea'
import { Tip } from './ui/tooltip'
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

// F-45: why a request is back with the Lead after the Master's approve found its files changed since the review.
export function SentBackNote({ c }: { c: Commit }) {
  const note = sentBackNote(c)
  return note ? (
    <div data-sent-back className="mt-2 rounded-md border border-amber-200 bg-amber-50 text-amber-900 px-2 py-1.5 text-caption break-words">
      {note}
    </div>
  ) : null
}

// State lines, note field and Approve / Reject. There is no "commit anyway": a changed file sends the request back to the Lead.
export function CommitActions({ c, d, groupWith = [] }: { c: Commit; d: Decision; groupWith?: number[] }) {
  const reviewable = c.state === 'master_review'
  const grouped = groupWith.length > 0
  return (
    <>
      {c.state === 'lead_review' && <div className="mt-2 text-caption text-gray-500">Waiting on Lead review.</div>}
      <SentBackNote c={c} />
      {(c.state === 'rejected' || c.state === 'failed') && c.note && (
        <pre className="mt-2 whitespace-pre-wrap font-mono text-code text-red-700 bg-red-50 rounded-md border border-red-100 px-2 py-1">{c.note}</pre>
      )}
      {c.state === 'withdrawn' && (
        <div data-withdrawn className="mt-2 text-gray-600">
          Withdrawn{c.note ? `: ${c.note}` : ''}
        </div>
      )}
      {c.state === 'committed' && c.sha && (
        <div className="mt-2 text-caption text-gray-600">
          Committed as <code className="font-mono text-code bg-gray-100 rounded-sm px-1">{c.sha}</code>
        </div>
      )}

      {grouped && reviewable && (
        <div data-group-note className="mt-2 text-caption text-gray-600">
          Part of a commit group with {groupWith.map((n) => `#${n}`).join(', ')}: commit it from the Inbox.
        </div>
      )}

      {reviewable && d.canDecide && (
        <div className="mt-2 flex flex-col gap-1.5">
          <Input value={d.note} onChange={(e) => d.setNote(e.target.value)} placeholder="Note (optional; shown to the requester on reject)" />
          <div className="flex items-center gap-2 flex-wrap">
            {!grouped && (
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

// F-11: the open requests this one is linked to (it requires them, or they require it), from the commits embedded in
// the loaded messages. A partner that isn't loaded counts as open; committed and rejected ones don't hold it back.
export function useGroupPartners(c: Commit): number[] {
  return useAppSelector((s) => {
    const known = new Map(s.messages.items.flatMap((m) => (m.commit ? [[m.commit.id, m.commit] as const] : [])))
    const ids = new Set([...(c.requires ?? []), ...[...known.values()].filter((x) => x.requires?.includes(c.id)).map((x) => x.id)])
    return [...ids].filter((id) => !['committed', 'rejected', 'superseded', 'withdrawn'].includes(String(known.get(id)?.state ?? ''))).sort((a, b) => a - b)
  }, (a, b) => a.length === b.length && a.every((x, i) => x === b[i]))
}

export default function CommitCard({ c, showFeature = false }: { c: Commit; showFeature?: boolean }) {
  const ctx = useCards()
  const d = useCommitDecision(c)
  const feature = useAppSelector((s) => selectFeatureLabel(s, c.feature_id))
  const files = Array.isArray(c.files) ? c.files : []
  const lead = leadReview(c)
  const groupWith = useGroupPartners(c)

  return (
    <div data-card={`commit-${c.id}`} className="mt-1.5 rounded-md border border-gray-200 bg-white px-3 py-2.5 text-meta">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-caption uppercase tracking-wide text-gray-500 font-semibold">Commit #{c.id}</span>
        <Badge className={stateChip(c.state)}>{stateLabel(c.state)}</Badge>
        {lead && (
          <Tip content={lead.note ? <span className="whitespace-pre-wrap">{lead.note}</span> : null}>
            <Badge data-lead={lead.tone} className={LEAD_TONE[lead.tone]}>
              {lead.label}
            </Badge>
          </Tip>
        )}
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
          <li key={f}>{f}</li>
        ))}
      </ul>

      <CommitMessage c={c} d={d} />
      <FilePreviews texts={[c.message]} featureId={c.feature_id} />

      {ctx && (
        <div className="mt-1.5">
          <Button variant="link" size="inline" className="text-caption" onClick={() => ctx.onReview(c.id)}>
            Review
          </Button>
        </div>
      )}

      <CommitActions c={c} d={d} groupWith={groupWith} />
    </div>
  )
}
