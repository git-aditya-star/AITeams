import { useState } from 'react'
import type { Approval } from '../api'
import { api, fmtAgo } from '../api'
import { PHASES, roleName, stateChip, stateLabel } from '../roles'
import { useAppDispatch, useAppSelector } from '../store'
import { approvalUpdated } from '../store/actions'
import { selectFeatureLabel, selectPid } from '../store/selectors'
import Markdown from './Markdown'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'

const UNCHANGED = 'unchanged' // Radix Select items cannot have an empty value

export default function ApprovalCard({ a, showFeature = false }: { a: Approval; showFeature?: boolean }) {
  const dispatch = useAppDispatch()
  const pid = useAppSelector(selectPid)
  const feature = useAppSelector((s) => selectFeatureLabel(s, a.feature_id))
  const [note, setNote] = useState('')
  const [phase, setPhase] = useState<string>(/done/i.test(a.title) ? 'done' : UNCHANGED)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const pending = a.state === 'pending'

  const decide = async (decision: 'approve' | 'changes') => {
    if (!pid) return
    setBusy(true)
    setErr(null)
    try {
      const body: { decision: 'approve' | 'changes'; note?: string; set_phase?: string } = { decision }
      if (note.trim()) body.note = note.trim()
      if (phase !== UNCHANGED) body.set_phase = phase
      const updated = await api.decideApproval(pid, a.id, body)
      // The WS `approval` frame also lands; apply the response when it looks like a row.
      if (updated && typeof updated === 'object' && 'id' in updated) dispatch(approvalUpdated({ ...a, ...updated }))
    } catch (e: any) {
      setErr(e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div data-card={`approval-${a.id}`} className="mt-1.5 rounded-md border border-gray-200 bg-white px-3 py-2.5 text-meta">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-caption uppercase tracking-wide text-gray-500 font-semibold">Approval #{a.id}</span>
        <Badge className={stateChip(a.state)}>{stateLabel(a.state)}</Badge>
        <span className="text-caption text-gray-400">
          {roleName(a.role)}
          {showFeature && feature ? ` · ${feature}` : ''}
          {' · '}
          {fmtAgo(a.created_at)}
        </span>
      </div>
      <div className="font-semibold mt-1">{a.title}</div>
      {a.summary && (
        <div className="mt-0.5 text-gray-700">
          <Markdown text={a.summary} />
        </div>
      )}
      {pending && pid ? (
        <div className="mt-2 flex flex-col gap-1.5">
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" />
          <div className="flex items-center gap-2 flex-wrap">
            <Button size="sm" disabled={busy} onClick={() => decide('approve')}>
              Approve
            </Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => decide('changes')}>
              Request changes
            </Button>
            <label className="flex items-center gap-1 text-caption text-gray-500 ml-1">
              set phase
              <Select value={phase} onValueChange={setPhase}>
                <SelectTrigger size="xs" aria-label="Set phase">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={UNCHANGED}>unchanged</SelectItem>
                  {PHASES.map((p) => (
                    <SelectItem key={p} value={p}>
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            {err && <span className="text-caption text-red-600">{err}</span>}
          </div>
        </div>
      ) : (
        a.note && (
          <div className="mt-1.5 text-gray-600">
            <span className="text-gray-400">Note:</span> {a.note}
          </div>
        )
      )}
    </div>
  )
}
