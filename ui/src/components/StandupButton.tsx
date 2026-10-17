import { useEffect, useState } from 'react'
import type { Standup } from '../api'
import { roleName } from '../roles'

// Standup button + status line; shows the "nothing changed" result inline for a few seconds.
export default function StandupButton({
  standup,
  onStart,
}: {
  standup: Standup | null
  onStart: () => Promise<Standup | { status: string }>
}) {
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
    <div className="flex items-center gap-2 text-xs flex-wrap">
      <button disabled={busy || !!active} onClick={() => void start()} className="rounded-md border border-gray-300 bg-white px-2 py-0.5 text-gray-700 hover:bg-gray-50 disabled:opacity-40">
        Standup
      </button>
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
