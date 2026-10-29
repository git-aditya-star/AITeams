import { useState } from 'react'
import type { Commit } from '../api'
import { api, sentBackFrom } from '../api'
import { useAppDispatch, useAppSelector } from '../store'
import { commitUpdated } from '../store/actions'
import { selectPid } from '../store/selectors'
import { sentBackToast } from '../toasts'
import { showToast } from './Toasts'

// F-45: the approve was refused because files changed after the Lead's review: say so. The Inbox drops the card and the
// conversation card turns to "Waiting on Lead review" from the commit and inbox frames, so nothing is removed here.
export const notifySentBack = (sentBack: Record<string, string[]>) => showToast(sentBackToast(sentBack), () => {})

// Approve / reject logic shared by CommitCard and CommitReview, so both behave the same.
// `onDecided` lets the review view update its own copy of the row too.
export function useCommitDecision(c: Commit, onDecided?: (c: Commit) => void) {
  const dispatch = useAppDispatch()
  const pid = useAppSelector(selectPid)
  const [message, setMessage] = useState(c.message)
  const [note, setNote] = useState('')
  const [stale, setStale] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const decide = async (decision: 'approve' | 'reject') => {
    if (!pid) return
    setBusy(true)
    setErr(null)
    try {
      const body: { decision: 'approve' | 'reject'; message?: string; note?: string } = { decision }
      if (decision === 'approve' && message.trim() && message !== c.message) body.message = message
      if (note.trim()) body.note = note.trim()
      const updated = await api.decideCommit(pid, c.id, body)
      if (updated && typeof updated === 'object' && 'id' in updated) {
        const row = { ...c, ...updated }
        dispatch(commitUpdated(row))
        onDecided?.(row)
      }
    } catch (e: any) {
      const s = sentBackFrom(e)
      if (s) {
        notifySentBack(s)
        // The review view keeps its own copy of the row, so it learns here that the request is back with the Lead.
        const paths = s[String(c.id)] ?? Object.values(s).flat()
        onDecided?.({ ...c, state: 'lead_review', note: `changed after review: ${paths.join(', ')}` })
      } else setErr(e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }

  return { message, setMessage, note, setNote, stale, setStale, busy, err, decide, canDecide: !!pid }
}
