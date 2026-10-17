import { useState } from 'react'
import type { Feature, Lock } from '../api'
import { PHASES, ROLES, phaseChip, roleName } from '../roles'
import { X } from './icons'

export type FeatureTab = 'chat' | 'docs'

export default function FeatureHeader({
  feature,
  locks,
  tab,
  error,
  onTab,
  onPatch,
  onReleaseLock,
  onFreshStart,
}: {
  feature: Feature
  locks: Lock[]
  tab: FeatureTab
  error: string | null
  onTab: (t: FeatureTab) => void
  onPatch: (patch: { phase?: string; owner?: string | null; archived?: boolean }) => Promise<void>
  onReleaseLock: (path: string) => Promise<void>
  onFreshStart: (mode: 'keep_docs' | 'start_over') => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [fresh, setFresh] = useState<null | 'choose' | 'confirm'>(null)
  const archived = !!feature.archived
  const run = (p: Promise<void>) => {
    setBusy(true)
    p.catch(() => {}).finally(() => setBusy(false))
  }
  const sel = 'rounded-md border border-gray-300 bg-white px-1.5 py-0.5 text-xs'
  const small = 'text-xs text-gray-500 hover:text-gray-800 border border-gray-300 rounded-md px-2 py-0.5 disabled:opacity-40'
  const freshStart = (mode: 'keep_docs' | 'start_over') => run(onFreshStart(mode).then(() => setFresh(null)))

  return (
    <div className="border-b border-gray-200 shrink-0">
      <div className="px-4 py-2 flex items-center gap-3 flex-wrap">
        <span className="font-mono text-xs text-gray-500">{feature.key}</span>
        <h1 className="font-semibold text-[15px] truncate max-w-[40%]">{feature.title}</h1>
        <label className="flex items-center gap-1 text-xs text-gray-500">
          phase
          <select value={feature.phase} disabled={busy} onChange={(e) => run(onPatch({ phase: e.target.value }))} className={`${sel} ${phaseChip(feature.phase)}`}>
            {PHASES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1 text-xs text-gray-500">
          owner
          <select value={feature.owner || ''} disabled={busy} onChange={(e) => run(onPatch({ owner: e.target.value || null }))} className={sel}>
            <option value="">none</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {roleName(r)}
              </option>
            ))}
          </select>
        </label>
        <button disabled={busy} onClick={() => run(onPatch({ archived: !archived }))} className="text-xs text-gray-500 hover:text-gray-800 border border-gray-300 rounded-md px-2 py-0.5 disabled:opacity-40">
          {archived ? 'Unarchive' : 'Archive'}
        </button>
        <button disabled={busy} onClick={() => setFresh(fresh ? null : 'choose')} className={small}>
          Fresh start
        </button>
        <div className="flex gap-1 text-[13px] ml-auto">
          {(['chat', 'docs'] as const).map((t) => (
            <button key={t} onClick={() => onTab(t)} className={`px-2 py-0.5 rounded-md ${tab === t ? 'bg-gray-100 font-medium text-gray-900' : 'text-gray-500 hover:text-gray-800'}`}>
              {t === 'chat' ? 'Chat' : 'Docs'}
            </button>
          ))}
        </div>
        {error && <span className="text-xs text-red-600 truncate">{error}</span>}
      </div>
      {fresh && (
        <div className="px-4 pb-2 flex items-center gap-3 text-xs flex-wrap">
          {fresh === 'choose' ? (
            <>
              <button disabled={busy} onClick={() => freshStart('keep_docs')} className={small}>
                Keep docs
              </button>
              <span className="text-gray-500">conversation restarts from here; docs, phase and owner stay</span>
              <button disabled={busy} onClick={() => setFresh('confirm')} className={small}>
                Start over
              </button>
              <span className="text-gray-500">docs archived, back to intake, the original request is re-posted</span>
            </>
          ) : (
            <>
              <span className="text-gray-700">Archive docs and restart?</span>
              <button disabled={busy} onClick={() => freshStart('start_over')} className="rounded-md bg-red-600 text-white px-2.5 py-0.5 font-medium hover:bg-red-700 disabled:opacity-40">
                Yes, start over
              </button>
            </>
          )}
          <button onClick={() => setFresh(null)} className="text-gray-500 hover:text-gray-800">
            Cancel
          </button>
        </div>
      )}
      {locks.length > 0 && (
        <div className="px-4 pb-2 flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] text-gray-400">locks</span>
          {locks.map((l) => (
            <span key={l.path} className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 px-2 py-px text-[11.5px] text-gray-700" title={`turn ${l.turn_id ?? '?'}`}>
              <span className="font-mono">{l.path}</span>
              <span className="text-gray-400">·</span>
              <span>{roleName(l.role)}</span>
              <button onClick={() => void onReleaseLock(l.path)} className="text-gray-400 hover:text-red-600 leading-none" title="force-release">
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
