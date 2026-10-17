import { useState } from 'react'
import type { TeamRole } from '../api'
import { Refresh, Stop } from './icons'

const btn = 'inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2 py-0.5 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-40'

// Stop + Fresh start for one role, with the inline fresh-start confirm. Used in the DM title bar
// and the dashboard Team section.
export default function RoleActions({
  t,
  onStop,
  onFreshStart,
}: {
  t: TeamRole | undefined
  onStop: () => Promise<void>
  onFreshStart: (handoff: boolean) => Promise<void>
}) {
  const [confirm, setConfirm] = useState(false)
  const [skip, setSkip] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const running = t?.state === 'running'
  const pending = !!t?.rotation

  const run = async (p: Promise<void>) => {
    setBusy(true)
    setErr(null)
    try {
      await p
      setConfirm(false)
    } catch (e: any) {
      setErr(typeof e?.detail === 'string' ? e.detail : e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-2 flex-wrap text-xs">
      {pending && (
        <span className="rounded-full bg-amber-50 text-amber-800 px-2 py-px text-[11px] font-medium" title={`rotation pending: ${t?.rotation}`}>
          {t?.rotation_handoff_pending ? 'handoff pending' : 'fresh session pending'}
        </span>
      )}
      {running && (
        <button disabled={busy} onClick={() => void run(onStop())} className={btn} title="SIGINT the running turn">
          <Stop size={12} /> Stop
        </button>
      )}
      {!confirm ? (
        <button disabled={busy || pending} onClick={() => setConfirm(true)} className={btn} title={pending ? 'a rotation is already pending' : 'retire the session; the next wake starts fresh'}>
          <Refresh size={12} /> Fresh start
        </button>
      ) : (
        <div className="flex flex-col gap-1 rounded-md border border-gray-200 bg-gray-50 px-2 py-1.5">
          <div className="text-gray-600">
            Retire this session; the next wake starts a fresh one from a handoff.
            {running && <span className="text-amber-700"> Takes effect after the current turn.</span>}
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1 text-gray-700 cursor-pointer select-none">
              <input type="checkbox" checked={skip} onChange={(e) => setSkip(e.target.checked)} /> skip handoff
            </label>
            <button disabled={busy} onClick={() => void run(onFreshStart(!skip))} className="rounded-md bg-blue-600 text-white px-2.5 py-0.5 font-medium hover:bg-blue-700 disabled:opacity-40">
              Confirm
            </button>
            <button onClick={() => setConfirm(false)} className="text-gray-500 hover:text-gray-800">
              Cancel
            </button>
          </div>
        </div>
      )}
      {err && <span className="text-red-600 truncate max-w-[240px]">{err}</span>}
    </div>
  )
}
