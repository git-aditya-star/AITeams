import { useState } from 'react'
import type { Commit } from '../api'
import { api, staleFrom } from '../api'
import { useCards } from './CardContext'

// Approve / reject logic shared by CommitCard and CommitReview, so both behave the same.
export function useCommitDecision(c: Commit) {
  const ctx = useCards()
  const [message, setMessage] = useState(c.message)
  const [note, setNote] = useState('')
  const [stale, setStale] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const decide = async (decision: 'approve' | 'reject', force = false) => {
    if (!ctx) return
    setBusy(true)
    setErr(null)
    try {
      const body: { decision: 'approve' | 'reject'; message?: string; note?: string; force?: boolean } = { decision }
      if (decision === 'approve' && message.trim() && message !== c.message) body.message = message
      if (note.trim()) body.note = note.trim()
      if (force) body.force = true
      const updated = await api.decideCommit(ctx.pid, c.id, body)
      if (updated && typeof updated === 'object' && 'id' in updated) ctx.onCommit({ ...c, ...updated })
    } catch (e: any) {
      const s = staleFrom(e)
      if (s) setStale(s)
      else setErr(e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }

  return { message, setMessage, note, setNote, stale, setStale, busy, err, decide }
}
