import { useState } from 'react'
import type { CommitGroup } from '../api'
import { api, sentBackFrom } from '../api'
import { roleName, stateChip, stateLabel } from '../roles'
import { useAppDispatch, useAppSelector } from '../store'
import { selectPid } from '../store/selectors'
import { refreshInbox } from '../store/thunks'
import { useCards } from './CardContext'
import FilePreviews from './FilePreviews'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Tip } from './ui/tooltip'
import { notifySentBack } from './useCommitDecision'

const subject = (message: string) => message.split('\n')[0]

// The readable part of a 409: a plain string detail, or the `detail` sentence inside an object detail.
const why = (e: any) => (typeof e?.detail === 'string' ? e.detail : typeof e?.detail?.detail === 'string' ? e.detail.detail : e?.message || 'failed')

// F-11: linked commit requests shown as one Inbox item with one Commit button: all members land in dependency
// order, or none do. Each member row opens that request's review; Reject stays per member, in its review.
export default function CommitGroupCard({ g }: { g: CommitGroup }) {
  const dispatch = useAppDispatch()
  const pid = useAppSelector(selectPid)
  const ctx = useCards()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const first = g.members[0]
  // Subjects already start with "F-n:" by convention; add the feature only when it's missing.
  const head = first ? subject(first.message) : ''
  const title = g.feature && !head.startsWith(`${g.feature}:`) ? `${g.feature}: ${head}` : head

  const commit = async () => {
    if (!pid) return
    setBusy(true)
    setErr(null)
    try {
      await api.decideCommit(pid, g.id, { decision: 'approve', group: true })
      dispatch(refreshInbox()) // the group leaves the Inbox; the members' frames update the conversations
    } catch (e: any) {
      // F-45: a changed file sends the whole group back to Lead review; the commit and inbox frames drop it from the Inbox.
      const s = sentBackFrom(e)
      if (s) notifySentBack(s)
      else setErr(why(e))
    } finally {
      setBusy(false)
    }
  }

  const notReady = g.blocked || (!g.ready && 'Every request in the group needs Lead approval first.')

  return (
    <div data-card={`commit-group-${g.id}`} className="mt-1.5 rounded-md border border-gray-200 bg-white px-3 py-2.5 text-meta">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-caption uppercase tracking-wide text-gray-500 font-semibold">Commit group</span>
        <span className="font-medium text-gray-900">{title}</span>
        <span className="text-caption text-gray-400">
          ({g.members.length} requests, {g.files} file{g.files === 1 ? '' : 's'})
        </span>
      </div>

      <ul className="mt-2 divide-y divide-gray-100 border-y border-gray-100">
        {g.members.map((m) => (
          <li key={m.id} data-member={m.id} className="flex items-center gap-2 py-1.5">
            <span className="font-mono text-code text-gray-500">#{m.id}</span>
            <span className="text-gray-700">{roleName(m.role)}</span>
            <span className="min-w-0 flex-1 truncate text-gray-900">{subject(m.message)}</span>
            <Badge className={stateChip(m.state)}>{stateLabel(m.state)}</Badge>
            {ctx && (
              <Button variant="link" size="inline" className="text-caption" onClick={() => ctx.onReview(m.id)}>
                Review
              </Button>
            )}
          </li>
        ))}
      </ul>

      <FilePreviews texts={g.members.map((m) => m.message)} featureId={first?.feature_id ?? null} />

      <div className="mt-2 flex items-center gap-2 flex-wrap">
        <Tip content={notReady || null}>
          {/* A disabled button gets no pointer events, so the tooltip hangs on a wrapper. */}
          <span tabIndex={notReady ? 0 : undefined} data-group-commit>
            <Button size="sm" disabled={busy || !!notReady} onClick={() => void commit()}>
              Commit group
            </Button>
          </span>
        </Tip>
        {err && <span className="text-caption text-red-600 break-words">{err}</span>}
      </div>
    </div>
  )
}
