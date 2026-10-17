import { createSelector } from '@reduxjs/toolkit'
import type { Feature, Question, TeamRole } from '../api'
import type { RootState } from './index'

export const selectPid = (s: RootState) => s.session.pid
export const selectFeatures = (s: RootState) => s.features
export const selectTeam = (s: RootState) => s.team
export const selectQuestions = (s: RootState) => s.questions

export const selectTeamByRole = createSelector([selectTeam], (team) => Object.fromEntries(team.map((t) => [t.role, t])) as Record<string, TeamRole>)
export const selectRole = (s: RootState, role: string) => s.team.find((t) => t.role === role)

export const isHistory = (f: Feature) => !!f.archived || f.phase === 'done'
const activity = (f: Feature) => f.last_activity ?? f.updated_at ?? f.created_at
// Sidebar "Features": not done or archived, most recently active first.
export const selectActiveFeatures = createSelector([selectFeatures], (fs) => fs.filter((f) => !isHistory(f)).sort((a, b) => activity(b) - activity(a)))
export const selectHistoryCount = createSelector([selectFeatures], (fs) => fs.filter(isHistory).length)
export const selectFeatureByConversation = (s: RootState, conversation: string) => s.features.find((f) => f.conversation_id === conversation)

export const featureLabel = (features: Feature[], id: number | null) => {
  const f = id == null ? undefined : features.find((x) => x.id === id)
  return f ? `${f.key} ${f.title}` : null
}
export const selectFeatureLabel = (s: RootState, id: number | null) => featureLabel(s.features, id)

// Banners and the DM footer are for blocking AskUserQuestion calls only; F-10 cards live in their message (and the Inbox).
const blocking = (q: Question) => q.kind !== 'card'
export const selectPendingQuestions = createSelector([selectQuestions], (qs) => qs.filter((q) => q.state === 'pending' && blocking(q)))
// A DM shows the role's pending questions plus its last three answered/expired ones.
export const selectDmQuestions = createSelector([selectQuestions, (_s: RootState, role: string | null) => role], (qs, role) => {
  if (!role) return []
  const mine = qs.filter((q) => q.role === role && blocking(q))
  return [...mine.filter((q) => q.state !== 'pending').slice(-3), ...mine.filter((q) => q.state === 'pending')]
})

export const selectInboxBadge = (s: RootState) => (s.inbox.data ? s.inbox.data.count : s.inbox.count)
export const selectUpgradeAvailable = (s: RootState) => {
  const h = s.session.health
  return !!h?.source_head && !!h?.sha && h.source_head !== h.sha
}
