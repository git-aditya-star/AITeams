import { useEffect, useState } from 'react'
import { roleName } from '../roles'
import { useAppDispatch, useAppSelector } from '../store'
import { startStandup } from '../store/thunks'
import { Button } from './ui/button'

// Standup button + status line (`size` default in the Dashboard header, next to New task); shows the "nothing changed" result inline for a few seconds.
export default function StandupButton({ size = 'xs' }: { size?: 'xs' | 'default' }) {
  const dispatch = useAppDispatch()
  const standup = useAppSelector((s) => s.dashboard?.standup ?? null)
  const onStart = () => dispatch(startStandup())
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const active = standup && (standup.state === 'collecting' || standup.state === 'compiling')

  useEffect(() => {
    if (!note) return
    const id = setTimeout(() => setNote(null), 5000)
    return () => clearTimeout(id)
  }, [note])

  const start = async () => {
    setBusy(true)
    setErr(null)
    try {
      const r = await onStart()
      if ('status' in r && r.status === 'nothing_changed') setNote('Nothing changed since the last standup.')
    } catch (e: any) {
      setErr(e?.status === 409 ? 'standup in progress' : e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-2 text-caption flex-wrap">
      <Button variant="outline" size={size} disabled={busy || !!active} onClick={() => void start()}>
        Standup
      </Button>
      {active && (
        <span className="text-gray-600">
          Standup #{standup.id}: {standup.state === 'compiling' ? 'compiling' : `waiting for ${standup.roles.map(roleName).join(', ')}`}
        </span>
      )}
      {note && <span className="text-gray-500">{note}</span>}
      {err && <span className="text-red-600">{err}</span>}
    </div>
  )
}
