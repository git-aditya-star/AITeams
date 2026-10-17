import type { Commit } from '../api'

export type LeadReview = { label: string; tone: 'approved' | 'own' | 'waiting'; note: string | null }

// F-7: what the Lead did with a commit request. `rejected` gets nothing: the Lead or the Master may have rejected it.
// The note (trimmed; blank means none) is the Lead's only while the request waits on the Master; decide_commit
// overwrites it afterwards.
export function leadReview(c: Commit): LeadReview | null {
  if (c.state === 'rejected') return null
  if (c.role === 'lead') return { label: "Lead's own, not reviewed", tone: 'own', note: null }
  if (c.state === 'lead_review') return { label: 'Waiting on Lead', tone: 'waiting', note: null }
  if (c.state === 'master_review' || c.state === 'committed' || c.state === 'failed')
    return { label: 'Lead approved', tone: 'approved', note: c.state === 'master_review' ? c.note?.trim() || null : null }
  return null
}

export const LEAD_TONE: Record<LeadReview['tone'], string> = {
  approved: 'bg-emerald-50 text-emerald-700',
  own: 'bg-amber-50 text-amber-800',
  waiting: 'bg-gray-100 text-gray-600',
}
