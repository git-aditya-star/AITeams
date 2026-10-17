import { useState } from 'react'
import { roleName } from '../roles'
import { Button } from './ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { Textarea } from './ui/textarea'

const STARTERS = ['product', 'lead', 'manager'] as const

// "New task" button that expands into the one-box form (sidebar and dashboard).
export default function NewTaskForm({
  onNewTask,
  buttonClass = 'w-full',
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
      <Button onClick={() => setCreating(true)} className={buttonClass}>
        New task
      </Button>
    )
  }
  return (
    <div className="flex flex-col gap-1.5">
      <Textarea
        autoFocus
        value={text}
        rows={3}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void submit()
          if (e.key === 'Escape') setCreating(false)
        }}
        placeholder="What should the team do?"
        className="resize-none"
      />
      <div className="flex gap-1 items-center">
        <Select value={starter} onValueChange={setStarter}>
          <SelectTrigger size="sm" aria-label="Starter" className="flex-1 min-w-0">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STARTERS.map((r) => (
              <SelectItem key={r} value={r}>
                {roleName(r)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button size="sm" disabled={busy || !text.trim()} onClick={submit}>
          Start
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setCreating(false)}>
          Cancel
        </Button>
      </div>
      {err && <div className="text-caption text-red-600 truncate">{err}</div>}
    </div>
  )
}
