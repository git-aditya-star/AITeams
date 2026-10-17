import { useEffect, useMemo, useRef, useState } from 'react'
import type { Commit, CommitDiff } from '../api'
import { api, fmtAgo } from '../api'
import { roleName, stateChip, stateLabel } from '../roles'
import { CardContext, featureLabel, useCards } from './CardContext'
import { CommitActions, CommitMessage } from './CommitCard'
import DiffFile, { Counts, StatusBadge, foldedByDefault } from './DiffFile'
import Markdown from './Markdown'
import { useCommitDecision } from './useCommitDecision'

const leadLine = (c: Commit) =>
  c.role === 'lead'
    ? "Lead's own request, no Lead review"
    : c.state === 'lead_review'
      ? 'Waiting on Lead review'
      : c.state === 'master_review' || c.state === 'committed' || c.state === 'failed'
        ? 'Approved by Lead'
        : null

// Full-width review of one commit request (F-3). Opened with `review=<id>`; `onClose` drops the param.
export default function CommitReview({ id, onClose }: { id: number; onClose: () => void }) {
  const ctx = useCards()
  const [data, setData] = useState<CommitDiff | null>(null)
  const [commit, setCommit] = useState<Commit | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!ctx) return
    setData(null)
    setCommit(null)
    setErr(null)
    api
      .commitDiff(ctx.pid, id)
      .then((r) => {
        setData(r)
        setCommit(r.commit ?? null)
      })
      .catch((e) => setErr(e?.message || String(e)))
  }, [ctx?.pid, id])

  // Esc closes, except while typing in the message or note.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (e.key === 'Escape' && !(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA'))) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // A decision taken here also updates this view, not only the cards.
  const inner = useMemo(
    () => (ctx ? { ...ctx, onCommit: (c: Commit) => { setCommit((old) => (old ? { ...old, ...c } : c)); ctx.onCommit(c) } } : null),
    [ctx],
  )

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-gray-50">
      <div className="px-4 py-2 border-b border-gray-200 bg-white flex items-center gap-3 shrink-0">
        <button onClick={onClose} className="text-[13px] text-blue-600 hover:underline">
          ← Back
        </button>
        <h1 className="font-semibold text-[15px]">Commit #{id}</h1>
        {commit && <span className={`text-[11px] rounded-full px-2 py-px font-medium ${stateChip(commit.state)}`}>{stateLabel(commit.state)}</span>}
      </div>
      <div className="flex-1 overflow-y-auto">
      {err ? (
        <div className="px-4 py-6 text-[13px] text-red-600">Could not load the commit request: {err}</div>
      ) : !data ? (
        <div className="px-4 py-6 text-[13px] text-gray-400">Loading…</div>
      ) : !commit ? (
        // Daemon without the F-3 diff fields: show the raw diff as before.
        <pre className="m-4 overflow-auto whitespace-pre font-mono text-[11.5px] bg-white rounded-md border border-gray-200 p-2">{data.diff || '(empty diff)'}</pre>
      ) : (
        <CardContext.Provider value={inner}>
          <Body key={commit.id} c={commit} data={data} />
        </CardContext.Provider>
      )}
      </div>
    </div>
  )
}

function Body({ c, data }: { c: Commit & { feature?: string | null }; data: CommitDiff }) {
  const ctx = useCards()
  const d = useCommitDecision(c)
  const files = data.files || []
  const overlap = data.overlap || []
  const totals = data.totals || {
    files: files.length,
    additions: files.reduce((n, f) => n + f.additions, 0),
    deletions: files.reduce((n, f) => n + f.deletions, 0),
  }
  const [open, setOpen] = useState<Record<string, boolean>>(() => Object.fromEntries(files.map((f) => [f.path, !foldedByDefault(f)])))
  const jumpTo = useRef<string | null>(null)
  const feature = (ctx && featureLabel(ctx.features, c.feature_id)) || c.feature || null
  const lead = leadLine(c)

  // Stale files from the diff endpoint; a 409 on approve refreshes them through the hook.
  useEffect(() => {
    d.setStale(data.stale || [])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  useEffect(() => {
    if (!jumpTo.current) return
    document.getElementById(`diff-${jumpTo.current}`)?.scrollIntoView({ block: 'start' })
    jumpTo.current = null
  })

  const jump = (path: string) => {
    jumpTo.current = path
    setOpen((o) => ({ ...o, [path]: true }))
  }

  return (
    <div className="px-4 py-3 flex flex-col gap-3 text-[13px]">
      <section className="rounded-md border border-gray-200 bg-white px-4 py-3">
        <div className="text-xs text-gray-500">
          {feature ? <span className="font-medium text-gray-700">{feature}</span> : 'No feature'} · requested by {roleName(c.role)} · {fmtAgo(c.created_at)}
          {c.sha && (
            <>
              {' · '}committed as <code className="font-mono bg-gray-100 rounded px-1">{c.sha}</code>
            </>
          )}
        </div>
        <div className="mt-2 text-[13px] text-gray-800">
          <Markdown text={c.message} />
        </div>
        {lead && (
          <div className="mt-2 text-xs text-gray-600">
            <span className="font-medium">{lead}</span>
            {lead === 'Approved by Lead' && c.state === 'master_review' && c.note ? `: ${c.note}` : ''}
          </div>
        )}
        <div className="mt-1 text-xs text-gray-600">
          {totals.files} file{totals.files === 1 ? '' : 's'} · <Counts a={totals.additions} d={totals.deletions} />
        </div>
        {d.stale.length > 0 && (
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 text-amber-900 px-2 py-1.5 text-xs">
            Files changed since the request: {d.stale.join(', ')}. Review the diff again before committing.
          </div>
        )}
        {overlap.length > 0 && (
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 text-amber-900 px-2 py-1.5 text-xs flex flex-col gap-0.5">
            {overlap.map((o) => (
              <div key={`${o.commit_id}:${o.path}`}>
                <code className="font-mono">{o.path}</code> is also in{' '}
                <button onClick={() => ctx?.onReview(o.commit_id)} className="text-blue-700 hover:underline">
                  #{o.commit_id}
                </button>{' '}
                ({[roleName(o.role), o.feature, o.state === 'lead_review' ? 'Lead review' : o.state === 'master_review' ? 'Master review' : stateLabel(o.state)].filter(Boolean).join(', ')})
              </div>
            ))}
          </div>
        )}
      </section>

      {c.state === 'master_review' && (
        <section className="rounded-md border border-gray-200 bg-white px-4 py-3">
          <div className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold">Commit message</div>
          <CommitMessage c={c} d={d} />
          <CommitActions c={c} d={d} showStale={false} />
        </section>
      )}
      {(c.state === 'rejected' || c.state === 'failed') && c.note && (
        <pre className="whitespace-pre-wrap font-mono text-xs text-red-700 bg-red-50 rounded-md border border-red-100 px-3 py-2">{c.note}</pre>
      )}

      {files.length > 0 && (
        <section className="rounded-md border border-gray-200 bg-white py-1">
          {files.map((f) => (
            <button key={f.path} onClick={() => jump(f.path)} className="w-full flex items-center gap-2 px-4 py-0.5 text-left hover:bg-gray-50">
              <span className={`font-mono text-xs truncate ${d.stale.includes(f.path) ? 'text-amber-700' : 'text-gray-800'}`}>{f.path}</span>
              <StatusBadge status={f.status} />
              {f.binary && <StatusBadge status="binary" />}
              <span className="ml-auto">
                <Counts a={f.additions} d={f.deletions} />
              </span>
            </button>
          ))}
        </section>
      )}

      {files.map((f) => (
        <DiffFile key={f.path} f={f} open={!!open[f.path]} onToggle={() => setOpen((o) => ({ ...o, [f.path]: !o[f.path] }))} />
      ))}
    </div>
  )
}
