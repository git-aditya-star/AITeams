import { useState } from 'react'
import type { Commit } from '../api'
import { api, fmtAgo, staleFrom } from '../api'
import { roleName, stateChip, stateLabel } from '../roles'
import { featureLabel, useCards } from './CardContext'

const btn = 'rounded-md px-3 py-1 text-[13px] font-medium disabled:opacity-40'

export default function CommitCard({ c, showFeature = false }: { c: Commit; showFeature?: boolean }) {
  const ctx = useCards()
  const [message, setMessage] = useState(c.message)
  const [note, setNote] = useState('')
  const [diff, setDiff] = useState<{ open: boolean; text: string | null; loading: boolean }>({ open: false, text: null, loading: false })
  const [stale, setStale] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const feature = ctx ? featureLabel(ctx.features, c.feature_id) : null
  const files = Array.isArray(c.files) ? c.files : []
  const reviewable = c.state === 'master_review'

  const toggleDiff = async () => {
    if (!ctx) return
    if (diff.open) return setDiff((d) => ({ ...d, open: false }))
    setDiff({ open: true, text: diff.text, loading: diff.text == null })
    if (diff.text == null) {
      try {
        const r = await api.commitDiff(ctx.pid, c.id)
        setDiff({ open: true, text: r.diff || '(empty diff)', loading: false })
        setStale(r.stale || [])
      } catch (e: any) {
        setDiff({ open: true, text: `could not load diff: ${e?.message || e}`, loading: false })
      }
    }
  }

  const decide = async (decision: 'approve' | 'reject', force = false) => {
    if (!ctx) return
    setBusy(true)
    setErr(null)
    try {
      const body: { decision: 'approve' | 'reject'; message?: string; note?: string; force?: boolean } = { decision }
      if (decision === 'approve' && message.trim() && message !== c.message) body.message = message
      if (note.trim()) body.note = note.trim()
      if (force) body.force = true
      const updated = await api.decideCommit(ctx.pid, c.id, body)
      if (updated && typeof updated === 'object' && 'id' in updated) ctx.onCommit({ ...c, ...updated })
    } catch (e: any) {
      const s = staleFrom(e)
      if (s) setStale(s)
      else setErr(e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }

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
          <li key={f} className={stale.includes(f) ? 'text-amber-700' : ''}>
            {f}
            {stale.includes(f) && <span className="ml-1 text-[11px] text-amber-700 font-sans">(changed since request)</span>}
          </li>
        ))}
      </ul>

      {reviewable ? (
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={Math.min(8, Math.max(2, message.split('\n').length))}
          className="mt-2 w-full resize-y rounded-md border border-gray-300 px-2 py-1 font-mono text-xs focus:outline-none focus:border-blue-400"
        />
      ) : (
        <pre className="mt-2 whitespace-pre-wrap font-mono text-xs text-gray-700 bg-gray-50 rounded-md border border-gray-100 px-2 py-1">{c.message}</pre>
      )}

      <div className="mt-1.5">
        <button onClick={toggleDiff} className="text-xs text-blue-600 hover:underline">
          {diff.open ? 'Hide diff' : 'Show diff'}
        </button>
        {diff.open && (
          <pre className="mt-1 max-h-96 overflow-auto whitespace-pre font-mono text-[11.5px] leading-snug bg-gray-50 rounded-md border border-gray-200 p-2">
            {diff.loading ? 'Loading…' : diff.text}
          </pre>
        )}
      </div>

      {stale.length > 0 && reviewable && (
        <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 text-amber-900 px-2 py-1.5 text-xs">
          Files changed since the request: {stale.join(', ')}. Review the diff again before committing.
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
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note (optional; shown to the requester on reject)"
            className="rounded-md border border-gray-300 px-2 py-1 text-[13px] focus:outline-none focus:border-blue-400"
          />
          <div className="flex items-center gap-2 flex-wrap">
            {stale.length > 0 ? (
              <button disabled={busy} onClick={() => decide('approve', true)} className={`${btn} bg-amber-600 text-white hover:bg-amber-700`}>
                Commit anyway
              </button>
            ) : (
              <button disabled={busy} onClick={() => decide('approve')} className={`${btn} bg-blue-600 text-white hover:bg-blue-700`}>
                Approve
              </button>
            )}
            <button disabled={busy} onClick={() => decide('reject')} className={`${btn} border border-gray-300 text-gray-700 hover:bg-gray-50`}>
              Reject
            </button>
            {err && <span className="text-xs text-red-600 truncate">{err}</span>}
          </div>
        </div>
      )}
    </div>
  )
}
