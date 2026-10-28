import { FileText } from 'lucide-react'
import { useState } from 'react'
import type { Approval, ApprovalDecision } from '../api'
import { api, fmtAgo } from '../api'
import { approvalType, roleName, stateChip, stateLabel } from '../roles'
import { useAppDispatch, useAppSelector } from '../store'
import { approvalUpdated } from '../store/actions'
import { selectDocPanel, selectFeatureLabel, selectPid } from '../store/selectors'
import { docPanel } from '../store/slices'
import BranchCard, { branchPayload, isBranchTitle, type BranchEdit } from './BranchCard'
import Markdown from './Markdown'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Input } from './ui/input'

// F-40: an approval filed before the brief existed whose whole summary is one `.aiteams/features/F-n/<file>` path still gets a
// button that opens that file.
const PATH_SUMMARY = /^\s*`?\.aiteams\/features\/F-\d+\/([^/\s`]+)`?\s*$/
const docOf = (a: Approval) => (a.brief ? a.brief.doc : (PATH_SUMMARY.exec(a.summary)?.[1] ?? null))

// What the Master is asked, readable at a glance: the type, which feature, who from, then (with a brief) three labelled parts. An
// approval from before the brief renders its title and summary as it always did.
export default function ApprovalCard({ a }: { a: Approval }) {
  const dispatch = useAppDispatch()
  const pid = useAppSelector(selectPid)
  const feature = useAppSelector((s) => selectFeatureLabel(s, a.feature_id))
  const open = useAppSelector(selectDocPanel)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const pending = a.state === 'pending'
  const type = approvalType(a.title)
  const brief = a.brief
  const doc = a.feature_id != null ? docOf(a) : null
  // F-39: a `Branch:` card carries the Lead's proposal; Approve sends the Master's edited choice.
  const branch = isBranchTitle(a.title) ? branchPayload(a) : null
  const [branchEdit, setBranchEdit] = useState<BranchEdit | null>(null)

  const decide = async (decision: ApprovalDecision) => {
    if (!pid) return
    setBusy(true)
    setErr(null)
    try {
      const body: { decision: ApprovalDecision; note?: string; payload?: BranchEdit['choice'] } = { decision }
      if (note.trim()) body.note = note.trim()
      if (branch && decision === 'approve' && branchEdit) body.payload = branchEdit.choice
      const updated = await api.decideApproval(pid, a.id, body)
      // The WS `approval` frame also lands; apply the response when it looks like a row.
      if (updated && typeof updated === 'object' && 'id' in updated) dispatch(approvalUpdated({ ...a, ...updated }))
    } catch (e: any) {
      // A refused approval (a worktree that would not be created) says why in `detail`, not the raw "POST … -> 409 {…}" line.
      setErr(typeof e?.detail === 'string' ? e.detail : e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }

  const docButton = doc && a.feature_id != null && (
    <Button
      variant="outline"
      size="xs"
      data-doc-open={doc}
      className={open?.featureId === a.feature_id && open.name === doc ? 'ring-2 ring-blue-200' : ''}
      onClick={() => dispatch(docPanel.actions.opened({ featureId: a.feature_id!, name: doc }))}
    >
      <FileText />
      Open {doc}
    </Button>
  )
  // The title minus its type prefix, unless the feature line already says it ("Design: X" on feature "F-n X").
  const rest = brief && type.rest && !(feature ?? '').endsWith(type.rest) ? type.rest : null

  return (
    <div data-card={`approval-${a.id}`} className="mt-1.5 rounded-md border border-gray-200 bg-white px-3 py-2.5 text-meta">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge data-approval-type={type.label} className={`px-2 font-semibold ${type.chip}`}>
          {type.label}
        </Badge>
        <span data-approval-feature className="font-semibold">
          {feature ?? type.rest}
        </span>
        <span className="text-gray-500">· from {roleName(a.role)}</span>
        {!pending && (
          <Badge data-state-chip className={stateChip(a.state)}>
            {stateLabel(a.state)}
          </Badge>
        )}
        <span className="ml-auto text-caption text-gray-400">
          #{a.id} · {fmtAgo(a.created_at)}
        </span>
      </div>
      {rest && <div className="mt-0.5 text-gray-600">{rest}</div>}
      {brief ? (
        <dl data-approval-brief className="mt-2 grid grid-cols-[104px_1fr] gap-x-3 gap-y-1.5">
          <dt className="pt-0.5 text-caption font-medium uppercase tracking-wide text-gray-500">What</dt>
          <dd data-brief="what">{brief.what}</dd>
          <dt className="pt-0.5 text-caption font-medium uppercase tracking-wide text-gray-500">If you approve</dt>
          <dd data-brief="if_approved">{brief.if_approved}</dd>
          <dt className="pt-0.5 text-caption font-medium uppercase tracking-wide text-gray-500">Trade-offs</dt>
          <dd data-brief="tradeoffs">
            <ul className="list-disc space-y-0.5 pl-4">
              {brief.tradeoffs.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          </dd>
        </dl>
      ) : (
        <>
          <div className="mt-1 font-medium">{a.title}</div>
          {a.summary && (
            <div className="mt-0.5 text-gray-700">
              <Markdown text={a.summary} />
            </div>
          )}
        </>
      )}
      {branch && <BranchCard p={branch} pending={pending} onEdit={setBranchEdit} />}
      {pending && pid ? (
        <div className="mt-3 flex flex-col gap-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            {docButton}
            <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" className="h-7 min-w-36 flex-1" />
            <Button size="sm" data-action="approve" disabled={busy || !!(branch && branchEdit?.blocked)} onClick={() => decide('approve')}>
              Approve
            </Button>
            <Button variant="outline" size="sm" data-action="changes" disabled={busy} onClick={() => decide('changes')}>
              Request changes
            </Button>
            <Button variant="outline" size="sm" data-action="reject" disabled={busy} className="text-red-700 hover:text-red-800" onClick={() => decide('reject')}>
              Reject
            </Button>
          </div>
          {err && <span className="text-caption text-red-600">{err}</span>}
        </div>
      ) : a.state === 'withdrawn' ? (
        <div data-withdrawn className="mt-1.5 text-gray-600">
          Withdrawn{a.note ? `: ${a.note}` : ''}
        </div>
      ) : (
        <>
          {docButton && <div className="mt-2">{docButton}</div>}
          {a.note && (
            <div className="mt-1.5 text-gray-600">
              <span className="text-gray-400">Note:</span> {a.note}
            </div>
          )}
        </>
      )}
    </div>
  )
}
