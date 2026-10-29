import type { Commit } from '../api'
import { isLead } from '../roles'

export type LeadReview = { label: string; tone: 'approved' | 'own' | 'waiting'; note: string | null }

// F-7: what the Lead did with a commit request. `rejected` and `withdrawn` get nothing: the Lead or the Master may have
// rejected it, and a withdrawn one (F-34) may have been pulled back at any stage, so what the Lead did is not known.
// The note (trimmed; blank means none) is the Lead's only while the request waits on the Master; decide_commit
// overwrites it afterwards.
export function leadReview(c: Commit): LeadReview | null {
  if (c.state === 'rejected' || c.state === 'withdrawn') return null
  if (isLead(c.role)) return { label: "Lead's own, not reviewed", tone: 'own', note: null }
  if (c.state === 'lead_review') return { label: 'Waiting on Lead', tone: 'waiting', note: null }
  if (c.state === 'master_review' || c.state === 'committed' || c.state === 'failed')
    return { label: 'Lead approved', tone: 'approved', note: c.state === 'master_review' ? c.note?.trim() || null : null }
  return null
}

// F-45: the note a request carries when files changed after the Lead's review and the Master's approve sent it back
// (`changed after review: <paths>`). Only while it waits on the Lead again; the Lead's re-approve overwrites it.
export const sentBackNote = (c: Commit): string | null =>
  c.state === 'lead_review' && c.note?.startsWith('changed after review') ? c.note.trim() : null

export const LEAD_TONE: Record<LeadReview['tone'], string> = {
  approved: 'bg-emerald-50 text-emerald-700',
  own: 'bg-amber-50 text-amber-800',
  waiting: 'bg-gray-100 text-gray-600',
}
