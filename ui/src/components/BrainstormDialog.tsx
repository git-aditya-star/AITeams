import { useEffect, useState } from 'react'
import type { Feature } from '../api'
import { api } from '../api'
import { useAppSelector } from '../store'
import { selectPid } from '../store/selectors'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'
import { Textarea } from './ui/textarea'

// F-10: the Master starts a brainstorm round on a feature. The round's System line and the ideas arrive as normal
// messages; Product's summary arrives as a question card.
export default function BrainstormDialog({ feature, open, onOpenChange }: { feature: Feature; open: boolean; onOpenChange: (open: boolean) => void }) {
  const pid = useAppSelector(selectPid)
  const [topic, setTopic] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) setErr(null)
  }, [open])

  const start = async () => {
    if (!pid || !topic.trim()) return
    setBusy(true)
    setErr(null)
    try {
      await api.startBrainstorm(pid, feature.id, topic.trim())
      setTopic('')
      onOpenChange(false)
    } catch (e: any) {
      setErr(e?.message || 'could not start the round') // e.g. 409: a round is already running on this feature
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" data-brainstorm-dialog>
        <DialogHeader>
          <DialogTitle>Brainstorm</DialogTitle>
          <DialogDescription>
            Every role with a voice on {feature.key} posts one idea on this topic. Product then sends you one card with the options.
          </DialogDescription>
        </DialogHeader>
        <Textarea
          aria-label="Topic"
          placeholder="What should the team brainstorm?"
          rows={3}
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void start()
          }}
        />
        {err && <div className="text-caption text-red-600 break-words">{err}</div>}
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={busy || !topic.trim()} onClick={start}>
            Start
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
