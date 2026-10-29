// Live-event toasts (F-1). Built only from WS frames, so nothing toasts on load or reconnect (but `sentBackToast`, F-45,
// which answers the Master's own click).
import type { Activity, Approval, Commit, Feature, Message, Question, RateLimitInfo, Standup } from './api'
import { fmtTime } from './api'
import { roleName } from './roles'
import type { WsFrame } from './ws'

export type Tone = 'action' | 'success' | 'warning' | 'error' | 'info'
export type Kind =
  | 'question'
  | 'blocker'
  | 'approval'
  | 'commit_request'
  | 'committed'
  | 'commit_failed'
  | 'sent_back'
  | 'standup'
  | 'stopped'
  | 'interrupted'
  | 'fresh_session'
  | 'rate_limit'
  | 'plugin_missing'

// Label and tone per kind; the toast's look follows the tone.
export const KINDS: Record<Kind, { label: string; tone: Tone }> = {
  question: { label: 'Question', tone: 'action' },
  blocker: { label: 'Blocker', tone: 'error' },
  approval: { label: 'Approval needed', tone: 'action' },
  commit_request: { label: 'Commit request', tone: 'action' },
  committed: { label: 'Committed', tone: 'success' },
  commit_failed: { label: 'Commit failed', tone: 'error' },
  sent_back: { label: 'Commit sent back', tone: 'warning' },
  standup: { label: 'Standup ready', tone: 'info' },
  stopped: { label: 'Turn stopped', tone: 'warning' },
  interrupted: { label: 'Interrupted', tone: 'warning' },
  fresh_session: { label: 'Fresh session', tone: 'info' },
  rate_limit: { label: 'Rate limited', tone: 'warning' },
  plugin_missing: { label: 'Plugin missing', tone: 'warning' },
}

export type Toast = {
  key: string
  kind: Kind
  text: string
  tone: Tone
  conversation?: string
  message?: number
}

const make = (kind: Kind, t: { key: string; text: string; conversation?: string; message?: number }): Toast => ({ ...t, kind, tone: KINDS[kind].tone })

export type ToastCtx = { features: Feature[]; prevRateLimit: RateLimitInfo | null }

const firstLine = (s: string | null | undefined) => (s || '').trim().split('\n')[0].trim()
// Whole body as one paragraph; the toast clamps it and shows the full text on hover.
const oneParagraph = (s: string | null | undefined) => (s || '').trim().replace(/\s+/g, ' ')

// F-45: the toast after a Master approve was refused because files changed after the Lead's review. It answers that action,
// not a WS frame; `sentBack` is the 409's {"<id>": [paths]}, and a repeat of the same ids replaces instead of stacking.
export const sentBackToast = (sentBack: Record<string, string[]>): Toast =>
  make('sent_back', {
    key: `sent_back:${Object.keys(sentBack).join(',')}`,
    text: `Sent back to Lead review: ${[...new Set(Object.values(sentBack).flat())].join(', ')}`,
  })

// Feature conversation for an approval/commit, else the requesting role's DM.
export const convFor = (featureId: number | null, role: string, features: Feature[]) =>
  features.find((f) => f.id === featureId)?.conversation_id || `dm-${role}`

let pluginSeq = 0

const ACTIVITY_KINDS: Kind[] = ['stopped', 'interrupted', 'fresh_session']

export function toastFor(f: WsFrame, ctx: ToastCtx): Toast | null {
  switch (f.event) {
    case 'question': {
      const q = f.data as Question
      if (q?.state !== 'pending') return null
      // A card opens its own conversation at its message; a blocking question shows in the role's DM.
      const at = q.kind === 'card' && q.conversation_id ? { conversation: q.conversation_id, message: q.message_id ?? undefined } : { conversation: `dm-${q.role}` }
      return make('question', { key: `question:${q.id}`, text: `${roleName(q.role)} asks: ${q.payload?.questions?.[0]?.question || ''}`, ...at })
    }
    case 'message': {
      // Same rule as db.inbox_messages.
      const m = f.data as Message
      if (!m || (m.type !== 'Question' && m.type !== 'Blocker') || !m.mentions?.includes('master') || m.author === 'master') return null
      const line = oneParagraph(m.text).replace(/^(@\w+[\s,]*)+/, '')
      const text = m.type === 'Blocker' ? `Blocker from ${roleName(m.author)}: ${line}` : `${roleName(m.author)} asks: ${line}`
      return make(m.type === 'Blocker' ? 'blocker' : 'question', { key: `msg:${m.id}`, text, conversation: m.conversation_id, message: m.id })
    }
    case 'approval': {
      const a = f.data as Approval
      if (a?.state !== 'pending') return null
      return make('approval', { key: `approval:${a.id}`, text: `Approval requested by ${roleName(a.role)}: ${a.title}`, conversation: convFor(a.feature_id, a.role, ctx.features) })
    }
    case 'commit': {
      const c = f.data as Commit
      if (!c) return null
      const subject = firstLine(c.message)
      const base = { key: `commit:${c.id}:${c.state}`, conversation: convFor(c.feature_id, c.role, ctx.features) }
      if (c.state === 'master_review') return make('commit_request', { ...base, text: `Commit request from ${roleName(c.role)}: ${subject}` })
      if (c.state === 'committed') return make('committed', { ...base, text: ['Committed', c.sha?.slice(0, 7), subject].filter(Boolean).join(' ') })
      if (c.state === 'failed') return make('commit_failed', { ...base, text: `Commit failed: ${subject}` })
      return null
    }
    case 'standup': {
      const s = f.data as Standup
      if (s?.state !== 'done') return null
      return make('standup', { key: `standup:${s.id}`, text: 'Standup report ready', conversation: 'standup' })
    }
    case 'activity': {
      const a = f.data as Activity
      if (!a || !ACTIVITY_KINDS.includes(a.kind as Kind)) return null
      // Activity text names the role in lowercase ("frontend's turn was stopped…"); show the display name.
      let text = a.role ? a.text.replace(new RegExp(`\\b${a.role}\\b`), roleName(a.role)) : a.text
      if (a.kind === 'fresh_session' && a.role) {
        const reason = a.text.match(/\(([^)]*)\)\s*$/)?.[1]
        text = `${roleName(a.role)} started a fresh session${reason ? ` (${reason})` : ''}`
      }
      return make(a.kind as Kind, { key: `activity:${a.id}`, text, conversation: a.role ? `dm-${a.role}` : a.conversation_id || undefined })
    }
    case 'plugin_missing': {
      // F-23: a plugin switched on in Role settings that a turn did not load. The daemon sends it once per flip;
      // the counter keeps a later flip from being swallowed by the "seen" set.
      const { id, role } = (f.data ?? {}) as { id?: string; role?: string }
      if (!id) return null
      return make('plugin_missing', { key: `plugin_missing:${id}:${++pluginSeq}`, text: `Plugin ${id} didn't load in ${role ? `${roleName(role)}'s` : 'a'} turn` })
    }
    case 'rate_limit': {
      const info = f.data?.info as RateLimitInfo | undefined
      if (!info || info.status.startsWith('allowed') || ctx.prevRateLimit) return null
      return make('rate_limit', { key: 'rate_limit', text: `Rate limited, turns held${info.resetsAt ? ` until ${fmtTime(info.resetsAt)}` : ''}` })
    }
  }
  return null
}
