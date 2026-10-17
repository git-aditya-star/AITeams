// Fetch helpers + types for the AITeams daemon API (same-origin).

export type Role = 'manager' | 'lead' | 'product' | 'frontend' | 'backend' | 'qa'
export type RoleState = 'idle' | 'queued' | 'running'
export type TurnState = 'queued' | 'running' | 'done' | 'failed' | 'interrupted'
export type ReceiptState = 'queued' | 'seen' | 'acked'
export type MessageType = 'Message' | 'Question' | 'Decision' | 'Concern' | 'Finding' | 'Handoff' | 'System'

export interface ProjectSummary {
  id: string
  name: string
  path: string
  is_git: boolean
  registered_at: number
}

export interface RateLimitInfo {
  status: string
  resetsAt?: number
  rateLimitType?: string
}

export interface Project extends ProjectSummary {
  paused: boolean
  rate_limited: RateLimitInfo | null
}

export interface Conversation {
  id: string
  kind: 'channel' | 'dm' | 'thread'
  title: string
  role: string | null
  created_at: number
  last_message_at: number | null
}

export interface Receipt {
  role: string
  state: ReceiptState
}

export interface Message {
  id: number
  conversation_id: string
  author: string
  type: MessageType | string
  text: string
  mentions: string[]
  turn_id: number | null
  created_at: number
  receipts: Receipt[]
}

export interface Turn {
  id: number
  role: string
  session_id: string | null
  state: TurnState
  origin: string
  conversation_id: string | null
  created_at: number
  started_at: number | null
  ended_at: number | null
  pid: number | null
  exit_code: number | null
  context_tokens: number
  cost_usd: number
  error: string | null
}

export interface TeamRole {
  role: string
  state: RoleState
  session_id: string | null
  last_turn: Turn | null
  context_tokens: number
  context_window: number
  context_pct: number
  cost_today: number
  model: string
}

export interface TurnEvent {
  turn_id: number
  seq: number
  ts: number
  type: string
  data: Record<string, any>
}

export interface QuestionOption {
  label: string
  description?: string
}

export interface QuestionItem {
  question: string
  header?: string
  multiSelect?: boolean
  options: QuestionOption[]
}

export interface Question {
  id: number
  turn_id: number
  role: string
  tool_use_id: string
  payload: { questions: QuestionItem[] }
  state: 'pending' | 'answered' | 'expired'
  answers: Record<string, string> | null
  created_at: number
  answered_at: number | null
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${init?.method || 'GET'} ${url} -> ${res.status} ${body}`)
  }
  return res.json() as Promise<T>
}

const post = <T,>(url: string, body: unknown) =>
  req<T>(url, { method: 'POST', body: JSON.stringify(body) })

export const api = {
  projects: () => req<ProjectSummary[]>('/api/projects'),
  project: (pid: string) => req<Project>(`/api/p/${pid}`),
  conversations: (pid: string) => req<Conversation[]>(`/api/p/${pid}/conversations`),
  createConversation: (pid: string, title: string) =>
    post<Conversation>(`/api/p/${pid}/conversations`, { title }),
  messages: (pid: string, conversation: string, before?: number, limit = 50) => {
    const q = new URLSearchParams({ conversation, limit: String(limit) })
    if (before != null) q.set('before', String(before))
    return req<Message[]>(`/api/p/${pid}/messages?${q}`)
  },
  sendMessage: (pid: string, conversation: string, text: string) =>
    post<Message>(`/api/p/${pid}/messages`, { conversation, text }),
  team: (pid: string) => req<{ roles: TeamRole[] }>(`/api/p/${pid}/team`),
  turns: (pid: string, role: string, limit = 50) =>
    req<Turn[]>(`/api/p/${pid}/turns?role=${role}&limit=${limit}`),
  turnEvents: (pid: string, tid: number, afterSeq = 0) =>
    req<TurnEvent[]>(`/api/p/${pid}/turns/${tid}/events?after_seq=${afterSeq}`),
  questions: (pid: string, state: Question['state']) =>
    req<Question[]>(`/api/p/${pid}/questions?state=${state}`),
  answerQuestion: (pid: string, qid: number, answers: Record<string, string>) =>
    post<Question>(`/api/p/${pid}/questions/${qid}/answer`, { answers }),
  pause: (pid: string, paused: boolean) => post<{ paused: boolean }>(`/api/p/${pid}/pause`, { paused }),
}

export function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

export function fmtCost(n: number): string {
  return `$${(n || 0).toFixed(4)}`
}

export function fmtDuration(secs: number): string {
  if (secs < 60) return `${Math.round(secs)}s`
  const m = Math.floor(secs / 60)
  const s = Math.round(secs % 60)
  return `${m}m ${String(s).padStart(2, '0')}s`
}
