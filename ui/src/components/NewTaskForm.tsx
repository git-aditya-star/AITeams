import { Plus } from 'lucide-react'
import { useState } from 'react'
import { roleName } from '../roles'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'
import { Textarea } from './ui/textarea'
import { ToggleGroup, ToggleGroupItem } from './ui/toggle-group'

const STARTERS = ['product', 'lead', 'manager'] as const

// F-15: the prominent "New task" button (sidebar and dashboard) and the modal where the Master writes the brief.
// The draft lives here, outside the dialog, so closing without starting keeps it for the next open.
export default function NewTaskForm({
  onNewTask,
  buttonClass = 'w-full',
  size = 'lg',
}: {
  onNewTask: (body: { text: string; starter: string }) => Promise<void>
  buttonClass?: string
  size?: 'lg' | 'default'
}) {
  const [open, setOpen] = useState(false)
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
      setOpen(false)
    } catch (e: any) {
      setErr(e?.message || 'failed') // the dialog and the text stay
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button size={size} onClick={() => setOpen(true)} className={buttonClass}>
        <Plus />
        New task
      </Button>
      <Dialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o)
          if (o) setErr(null)
        }}
      >
        <DialogContent className="sm:max-w-2xl p-6 gap-4" data-new-task>
          <DialogHeader>
            <DialogTitle className="text-title">What should the team build?</DialogTitle>
            <DialogDescription className="text-meta">Describe it in a few lines. Product will interview you for the details.</DialogDescription>
          </DialogHeader>
          <Textarea
            autoFocus
            aria-label="Brief"
            rows={6}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                void submit()
              }
            }}
            placeholder="Add CSV export to the reports page, so finance can pull last month's orders without asking us."
            className="field-sizing-content min-h-36 max-h-[50vh] resize-none px-3 py-2 text-body"
          />
          {err && <div className="text-caption text-red-600 break-words">{err}</div>}
          <div className="flex items-center gap-3">
            <span className="text-meta text-gray-500" id="new-task-starter">
              Starts with
            </span>
            {/* Radix sends '' when the active item is clicked again; ignore it so a starter is always picked. */}
            <ToggleGroup type="single" variant="outline" aria-labelledby="new-task-starter" value={starter} onValueChange={(v) => v && setStarter(v)}>
              {STARTERS.map((r) => (
                <ToggleGroupItem key={r} value={r} className="px-3">
                  {roleName(r)}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
          <DialogFooter className="items-center sm:justify-between">
            <span className="text-caption text-gray-400">⌘↵ to start</span>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button disabled={busy || !text.trim()} onClick={submit}>
                Start
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
