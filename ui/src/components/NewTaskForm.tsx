import { useState } from 'react'
import { roleName } from '../roles'

const STARTERS = ['product', 'lead', 'manager'] as const

// "New task" button that expands into the one-box form (sidebar and dashboard).
export default function NewTaskForm({
  onNewTask,
  buttonClass = 'w-full rounded-md bg-blue-600 text-white py-1.5 text-[13px] font-medium hover:bg-blue-700',
}: {
  onNewTask: (body: { text: string; starter: string }) => Promise<void>
  buttonClass?: string
}) {
  const [creating, setCreating] = useState(false)
  const [text, setText] = useState('')
  const [starter, setStarter] = useState<string>('product')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = async () => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true)
    setErr(null)
    try {
      await onNewTask({ text: t, starter })
      setText('')
      setCreating(false)
    } catch (e: any) {
      setErr(e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }

  if (!creating) {
    return (
      <button onClick={() => setCreating(true)} className={buttonClass}>
        New task
      </button>
    )
  }
  return (
    <div className="flex flex-col gap-1.5">
      <textarea
        autoFocus
        value={text}
        rows={3}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void submit()
          if (e.key === 'Escape') setCreating(false)
        }}
        placeholder="What should the team do?"
        className="w-full resize-none rounded-md border border-gray-300 px-2 py-1 text-[13px] focus:outline-none focus:border-blue-400"
      />
      <div className="flex gap-1 items-center">
        <select value={starter} onChange={(e) => setStarter(e.target.value)} className="flex-1 min-w-0 rounded-md border border-gray-300 bg-white px-1.5 py-1 text-xs" title="starter">
          {STARTERS.map((r) => (
            <option key={r} value={r}>
              {roleName(r)}
            </option>
          ))}
        </select>
        <button disabled={busy || !text.trim()} onClick={submit} className="rounded-md bg-blue-600 text-white px-2.5 py-1 text-xs font-medium hover:bg-blue-700 disabled:opacity-40">
          Start
        </button>
        <button onClick={() => setCreating(false)} className="text-xs text-gray-500 hover:text-gray-800 px-1">
          Cancel
        </button>
      </div>
      {err && <div className="text-[11px] text-red-600 truncate">{err}</div>}
    </div>
  )
}
