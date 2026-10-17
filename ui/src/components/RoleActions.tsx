import { RotateCw, Square } from 'lucide-react'
import { useState } from 'react'
import { useAppDispatch, useAppSelector } from '../store'
import { selectRole } from '../store/selectors'
import { freshStartRole, stopRole } from '../store/thunks'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Tip } from './ui/tooltip'

// Stop + Fresh start for one role, with the inline fresh-start confirm. Used in the DM title bar
// and the dashboard Team section.
export default function RoleActions({ role }: { role: string }) {
  const dispatch = useAppDispatch()
  const t = useAppSelector((s) => selectRole(s, role))
  const onStop = () => dispatch(stopRole(role))
  const onFreshStart = (handoff: boolean) => dispatch(freshStartRole(role, handoff))
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
    <div className="flex items-center gap-2 flex-wrap text-caption">
      {pending && (
        <Tip content={`rotation pending: ${t?.rotation}`}>
          <Badge className="bg-amber-50 text-amber-800">{t?.rotation_handoff_pending ? 'handoff pending' : 'fresh session pending'}</Badge>
        </Tip>
      )}
      {running && (
        <Tip content="SIGINT the running turn">
          <Button variant="outline" size="xs" disabled={busy} onClick={() => void run(onStop())}>
            <Square /> Stop
          </Button>
        </Tip>
      )}
      {!confirm ? (
        <Tip content={pending ? 'a rotation is already pending' : 'retire the session; the next wake starts fresh'}>
          {/* span: a disabled button gets no pointer events, so the tooltip hangs on the wrapper */}
          <span className="inline-flex">
            <Button variant="outline" size="xs" disabled={busy || pending} onClick={() => setConfirm(true)}>
              <RotateCw /> Fresh start
            </Button>
          </span>
        </Tip>
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
            <Button size="xs" disabled={busy} onClick={() => void run(onFreshStart(!skip))}>
              Confirm
            </Button>
            <Button variant="ghost" size="xs" onClick={() => setConfirm(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {err && <span className="text-red-600 truncate max-w-[240px]">{err}</span>}
    </div>
  )
}
