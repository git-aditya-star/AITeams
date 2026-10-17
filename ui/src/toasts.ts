// Live-event toasts (F-1). Built only from WS frames, so nothing toasts on load or reconnect.
import type { Activity, Approval, Commit, Feature, Message, Question, RateLimitInfo, Standup } from './api'
import { fmtTime } from './api'
import { roleName } from './roles'
import type { WsFrame } from './ws'

export type Toast = {
  key: string
  text: string
  tone: 'info' | 'error'
  conversation?: string
  message?: number
}

export type ToastCtx = { features: Feature[]; prevRateLimit: RateLimitInfo | null }

const firstLine = (s: string | null | undefined) => (s || '').trim().split('\n')[0].trim()
// Whole body as one paragraph; the toast clamps it and shows the full text on hover.
const oneParagraph = (s: string | null | undefined) => (s || '').trim().replace(/\s+/g, ' ')

// Feature conversation for an approval/commit, else the requesting role's DM.
const convFor = (featureId: number | null, role: string, features: Feature[]) =>
  features.find((f) => f.id === featureId)?.conversation_id || `dm-${role}`

const ACTIVITY_KINDS = ['stopped', 'interrupted', 'fresh_session']

export function toastFor(f: WsFrame, ctx: ToastCtx): Toast | null {
  switch (f.event) {
    case 'question': {
      const q = f.data as Question
      if (q?.state !== 'pending') return null
      return { key: `question:${q.id}`, text: `${roleName(q.role)} asks: ${q.payload?.questions?.[0]?.question || ''}`, tone: 'info', conversation: `dm-${q.role}` }
    }
    case 'message': {
      // Same rule as db.inbox_messages.
      const m = f.data as Message
      if (!m || (m.type !== 'Question' && m.type !== 'Blocker') || !m.mentions?.includes('master') || m.author === 'master') return null
      const line = oneParagraph(m.text).replace(/^(@\w+[\s,]*)+/, '')
      const text = m.type === 'Blocker' ? `Blocker from ${roleName(m.author)}: ${line}` : `${roleName(m.author)} asks: ${line}`
      return { key: `msg:${m.id}`, text, tone: 'info', conversation: m.conversation_id, message: m.id }
    }
    case 'approval': {
      const a = f.data as Approval
      if (a?.state !== 'pending') return null
      return { key: `approval:${a.id}`, text: `Approval requested by ${roleName(a.role)}: ${a.title}`, tone: 'info', conversation: convFor(a.feature_id, a.role, ctx.features) }
    }
    case 'commit': {
      const c = f.data as Commit
      if (!c) return null
      const subject = firstLine(c.message)
      const base = { key: `commit:${c.id}:${c.state}`, conversation: convFor(c.feature_id, c.role, ctx.features) }
      if (c.state === 'master_review') return { ...base, text: `Commit request from ${roleName(c.role)}: ${subject}`, tone: 'info' }
      if (c.state === 'committed') return { ...base, text: ['Committed', c.sha?.slice(0, 7), subject].filter(Boolean).join(' '), tone: 'info' }
      if (c.state === 'failed') return { ...base, text: `Commit failed: ${subject}`, tone: 'error' }
      return null
    }
    case 'standup': {
      const s = f.data as Standup
      if (s?.state !== 'done') return null
      return { key: `standup:${s.id}`, text: 'Standup report ready', tone: 'info', conversation: 'standup' }
    }
    case 'activity': {
      const a = f.data as Activity
      if (!a || !ACTIVITY_KINDS.includes(a.kind)) return null
      // Activity text names the role in lowercase ("frontend's turn was stopped…"); show the display name.
      let text = a.role ? a.text.replace(new RegExp(`\\b${a.role}\\b`), roleName(a.role)) : a.text
      if (a.kind === 'fresh_session' && a.role) {
        const reason = a.text.match(/\(([^)]*)\)\s*$/)?.[1]
        text = `${roleName(a.role)} started a fresh session${reason ? ` (${reason})` : ''}`
      }
      return { key: `activity:${a.id}`, text, tone: 'info', conversation: a.role ? `dm-${a.role}` : a.conversation_id || undefined }
    }
    case 'rate_limit': {
      const info = f.data?.info as RateLimitInfo | undefined
      if (!info || info.status.startsWith('allowed') || ctx.prevRateLimit) return null
      return { key: 'rate_limit', text: `Rate limited, turns held${info.resetsAt ? ` until ${fmtTime(info.resetsAt)}` : ''}`, tone: 'info' }
    }
  }
  return null
}
