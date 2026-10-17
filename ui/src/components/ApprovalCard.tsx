import { useState } from 'react'
import type { Approval } from '../api'
import { api, fmtAgo } from '../api'
import { PHASES, roleName, stateChip, stateLabel } from '../roles'
import { featureLabel, useCards } from './CardContext'
import Markdown from './Markdown'

const btn = 'rounded-md px-3 py-1 text-[13px] font-medium disabled:opacity-40'

export default function ApprovalCard({ a, showFeature = false }: { a: Approval; showFeature?: boolean }) {
  const ctx = useCards()
  const [note, setNote] = useState('')
  const [phase, setPhase] = useState<string>(/done/i.test(a.title) ? 'done' : '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const feature = ctx ? featureLabel(ctx.features, a.feature_id) : null
  const pending = a.state === 'pending'

  const decide = async (decision: 'approve' | 'changes') => {
    if (!ctx) return
    setBusy(true)
    setErr(null)
    try {
      const body: { decision: 'approve' | 'changes'; note?: string; set_phase?: string } = { decision }
      if (note.trim()) body.note = note.trim()
      if (phase) body.set_phase = phase
      const updated = await api.decideApproval(ctx.pid, a.id, body)
      // The WS `approval` frame also lands; apply the response when it looks like a row.
      if (updated && typeof updated === 'object' && 'id' in updated) ctx.onApproval({ ...a, ...updated })
    } catch (e: any) {
      setErr(e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-1.5 rounded-md border border-gray-200 bg-white px-3 py-2.5 text-[13px]">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold">Approval #{a.id}</span>
        <span className={`text-[11px] rounded-full px-2 py-px font-medium ${stateChip(a.state)}`}>{stateLabel(a.state)}</span>
        <span className="text-xs text-gray-400">
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
      {pending && ctx ? (
        <div className="mt-2 flex flex-col gap-1.5">
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note (optional)"
            className="rounded-md border border-gray-300 px-2 py-1 text-[13px] focus:outline-none focus:border-blue-400"
          />
          <div className="flex items-center gap-2 flex-wrap">
            <button disabled={busy} onClick={() => decide('approve')} className={`${btn} bg-blue-600 text-white hover:bg-blue-700`}>
              Approve
            </button>
            <button disabled={busy} onClick={() => decide('changes')} className={`${btn} border border-gray-300 text-gray-700 hover:bg-gray-50`}>
              Request changes
            </button>
            <label className="flex items-center gap-1 text-xs text-gray-500 ml-1">
              set phase
              <select value={phase} onChange={(e) => setPhase(e.target.value)} className="rounded-md border border-gray-300 bg-white px-1.5 py-0.5 text-xs">
                <option value="">unchanged</option>
                {PHASES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            {err && <span className="text-xs text-red-600">{err}</span>}
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
