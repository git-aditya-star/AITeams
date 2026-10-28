// Fetch helpers + types for the AITeams daemon API (same-origin).

export type Role = 'manager' | 'lead' | 'lead-2' | 'product' | 'frontend' | 'backend' | 'qa'
export type RoleState = 'idle' | 'queued' | 'running'
export type TurnState = 'queued' | 'running' | 'done' | 'failed' | 'interrupted'
export type ReceiptState = 'queued' | 'seen' | 'acked'
export type MessageType =
  | 'Message'
  | 'Question'
  | 'Decision'
  | 'Concern'
  | 'Finding'
  | 'Handoff'
  | 'System'
  | 'Blocker'
  | 'Approval'
  | 'Commit'
  | 'Standup'

export type WakeKind = 'normal' | 'handoff' | 'standup' | 'standup_compile' | 'brainstorm' | 'brainstorm_compile'

export type Phase = 'intake' | 'spec' | 'design' | 'build' | 'qa' | 'done'
export type ApprovalState = 'pending' | 'approved' | 'changes_requested' | 'rejected' | 'withdrawn' // F-25: `rejected` = the Master said no, drop it; F-34: `withdrawn` = the sender (or the feature's Lead) pulled it back, with a reason in `note`
export type CommitState = 'lead_review' | 'master_review' | 'rejected' | 'committed' | 'failed' | 'withdrawn'

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
  schema_ahead?: boolean
}

export interface Health {
  ok: boolean
  pid: number
  port: number
  running_turns: number
  version: string | null
  sha: string | null
  ref: string | null
  installed_at: number | null
  source: string | null
  source_head: string | null
}

export interface Conversation {
  id: string
  kind: 'channel' | 'dm' | 'feature'
  title: string
  role: string | null
  created_at: number
  last_message_at: number | null
  reset_after?: number
}

// F-6: one entry per emoji, roles in the order they reacted.
export interface Reaction {
  emoji: string
  roles: string[]
}

export interface Receipt {
  role: string
  state: ReceiptState
}

export interface Feature {
  id: number
  key: string
  title: string
  phase: Phase | string
  owner: string | null
  lead?: string | null // F-22: the Lead who acts in this feature (`lead` = Lead 1, `lead-2`); null until one is assigned
  conversation_id: string
  archived: boolean | number
  created_at: number
  updated_at: number
  last_activity: number | null
  open_approvals: number
  open_commits: number
  design?: FeatureDesign // F-21
}

// F-21: where the feature's design stands. It comes from the latest approval titled `Design:`; commit requests
// on a `gated` feature are refused until it is approved. `approval_id` is that approval (null when none).
export type DesignState = 'approved' | 'pending' | 'changes' | 'rejected' | 'none'
export interface FeatureDesign {
  state: DesignState
  approval_id: number | null
  gated: boolean
}

// F-29: a feature's docs are its .md files plus its mocks (html pages and images, served raw by `fileUrl`) and its published
// Claude artifacts (kind `artifact`: `name` is the title, `url` the claude.ai page, `path` the local source file).
// An older daemon sends no `kind`; that means md.
export type DocKind = 'md' | 'html' | 'image' | 'artifact'

export interface FeatureDoc {
  name: string
  kind?: DocKind
  size: number
  updated_at: number | null
  editor: string | null
  url?: string | null // artifact
  version?: number // artifact
  path?: string // artifact
}

// F-29 Part B: a page a role published to claude.ai after the Master confirmed it. Private; only the Master shares it.
export interface Artifact {
  id: number
  feature_id: number
  path: string // the local source, .aiteams/features/F-<n>/artifact-<slug>.html (or just its name)
  url: string | null
  title: string
  version: number
  role: string
  created_at: number
  updated_at: number
}

// F-25: what the Master can answer an approval card with.
export type ApprovalDecision = 'approve' | 'changes' | 'reject'

export interface Approval {
  id: number
  feature_id: number | null
  role: string
  turn_id: number | null
  title: string
  summary: string
  state: ApprovalState | string
  note: string | null
  message_id: number | null
  created_at: number
  resolved_at: number | null
}

export interface Commit {
  id: number
  feature_id: number | null
  role: string
  turn_id: number | null
  files: string[]
  message: string
  state: CommitState | string
  note: string | null
  sha: string | null
  message_id: number | null
  created_at: number
  updated_at: number
  requires?: number[] // F-11: ids this request must land after (a commit group)
}

// F-11: linked requests the Inbox shows as one item and commits together, all or nothing.
export interface CommitGroup {
  id: number // the lowest member id; any member id works for the group commit
  feature: string | null
  members: (Commit & { feature: string | null })[] // dependency order
  files: number // distinct files across the members still pending
  ready: boolean // every pending member is in master_review and none requires a rejected request
  blocked?: string | null // why it can't land, when a member requires a rejected request
}

// F-3: per-file structure returned by the commit diff endpoint.
export interface DiffFile {
  path: string
  status: 'modified' | 'added' | 'deleted' | 'unchanged' | 'missing' | string
  additions: number
  deletions: number
  binary: boolean
  size: number | null
  built: boolean
  text: string
}

export interface CommitOverlap {
  path: string
  commit_id: number
  role: string
  state: string
  feature: string | null
}

export interface CommitDiff {
  diff: string
  stale: string[]
  commit?: Commit & { feature: string | null }
  files?: DiffFile[]
  totals?: { files: number; additions: number; deletions: number }
  overlap?: CommitOverlap[]
}

export interface Lock {
  path: string
  role: string
  turn_id: number | null
  created_at: number
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
  reactions?: Reaction[]
  ref?: string | null
  approval?: Approval | null
  commit?: Commit | null
  question?: Question | null // F-10: ref question:<id>
  brainstorm?: Brainstorm | null // F-10: ref brainstorm:<id> (the round's System line)
  artifact?: Artifact | null // F-29: ref artifact:<id> (the link card)
}

// F-10: a brainstorm round on a feature: every voice posts one idea, then Product compiles one card.
export interface Brainstorm {
  id: number
  feature_id: number
  topic: string
  state: 'collecting' | 'compiling' | 'done' | 'failed'
  roles: string[]
  requested_by: string
  start_message_id: number | null
  question_id: number | null
  created_at: number
  ended_at: number | null
}

export interface InboxMessage extends Message {
  conversation_title: string
}

export interface Inbox {
  questions: Question[]
  approvals: Approval[]
  commits: Commit[]
  groups?: CommitGroup[] // F-11: members are left out of `commits`; a group counts as one
  messages: InboxMessage[]
  count: number
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
  kind?: WakeKind | string
  prompt?: string | null
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
  effort?: string | null // F-9: effective effort, null = no --effort
  model_label?: string // F-9 part 2: "Sonnet 5.5", or the raw ID when it isn't offered
  session_seq?: number | null
  fresh_starts?: number
  rotation?: string | null
  rotation_handoff_pending?: boolean
  current?: { conversation_id: string; label: string; feature_key: string | null } | null
  kind?: WakeKind | string | null
}

// M1c: activity feed, standups, handoffs, dashboard.
export interface Activity {
  id: number
  ts: number
  kind: string
  role: string | null
  feature_id: number | null
  conversation_id: string | null
  text: string
  ref: string | null
}

export interface Standup {
  id: number
  state: 'collecting' | 'compiling' | 'done' | 'failed' | string
  roles: string[]
  requested_by: string
  start_message_id: number | null
  path: string | null
  message_id: number | null
  created_at: number
  ended_at: number | null
}

export interface Handoff {
  id: number
  role: string
  session_id: string | null
  seq: number | null
  path: string
  message_id: number | null
  reason: string | null
  context_pct: number | null
  created_at: number
  text?: string
}

export interface WaitsOn {
  kind: 'approval' | 'question' | 'commit' | 'review' | 'running' | 'queued' | 'message' | string
  who: string
  since: number | null
}

export interface DashboardFeature extends Feature {
  waits_on: WaitsOn | null
  stale: boolean
}

export interface Dashboard {
  needs_you: { questions: number; approvals: number; commits: number; messages: number; total: number }
  phase_counts: Record<string, number>
  features: DashboardFeature[]
  team: TeamRole[]
  activity: Activity[]
  standup: Standup | null
  last_standup: Standup | null
  shipped?: Shipped // F-17
  activity_hourly?: HourBucket[] // F-18
}

// F-18: one local hour of team activity; 24 of them, oldest first, the last one the current hour.
export interface HourBucket {
  start: number
  messages: number // non-System messages
  commits: number // requests committed in that hour
}

// F-17: what the team finished. `done_at` is set when a feature reaches done or is archived, cleared on reopen.
export interface ShippedFeature {
  id: number
  key: string
  title: string
  summary: string | null // the `**Summary:**` line of its prd.md
  conversation_id: string
  done_at: number
}

export interface Shipped {
  week: number // since Monday 00:00 local time
  total: number
  recent: ShippedFeature[] // newest 10 by done_at
  timeline?: ShippedFeature[] // F-18: done in the last 14 days, newest first, at most 100
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
  state: 'pending' | 'answered' | 'expired' | 'withdrawn'
  answers: Record<string, string> | null
  created_at: number
  answered_at: number | null
  // F-10: 'card' = non-blocking ask_master card (lives in a message, never expires); 'blocking' = held AskUserQuestion.
  kind?: 'blocking' | 'card'
  purpose?: 'ask' | 'interview' | 'concern' | 'a_vs_b' | 'brainstorm' | null
  intro?: string | null
  feature_id?: number | null
  conversation_id?: string | null
  message_id?: number | null
  note?: string | null // F-34: why a card was withdrawn (absent from an older daemon)
}

// Thrown for non-2xx responses; `detail` is the parsed JSON `detail` when the body was JSON
// (e.g. a 409 on commit approve carries `{detail: {stale: [...]}}`).
export class ApiError extends Error {
  status: number
  detail: any
  constructor(message: string, status: number, detail: any) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (res.status === 401) {
    // Cookie missing or rotated: the SPA route now serves the sign-in page. Reload once per tab, so the Vite
    // dev origin (which serves index.html regardless) does not loop.
    let again = false
    try {
      again = sessionStorage.getItem('aiteams401') === '1'
      sessionStorage.setItem('aiteams401', '1')
    } catch {
      /* storage unavailable */
    }
    if (!again) location.reload()
    throw new ApiError(`${init?.method || 'GET'} ${url} -> 401 (sign in: aiteams token --link)`, 401, null)
  }
  try {
    sessionStorage.removeItem('aiteams401')
  } catch {
    /* storage unavailable */
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    let detail: any = null
    try {
      detail = JSON.parse(body)?.detail ?? null
    } catch {
      /* not JSON */
    }
    throw new ApiError(`${init?.method || 'GET'} ${url} -> ${res.status} ${body}`, res.status, detail)
  }
  return res.json() as Promise<T>
}

const post = <T,>(url: string, body: unknown) =>
  req<T>(url, { method: 'POST', body: JSON.stringify(body) })
const put = <T,>(url: string, body: unknown) =>
  req<T>(url, { method: 'PUT', body: JSON.stringify(body) })
const patch = <T,>(url: string, body: unknown) =>
  req<T>(url, { method: 'PATCH', body: JSON.stringify(body) })
const del = <T,>(url: string) => req<T>(url, { method: 'DELETE' })

// F-9: global role settings (~/.aiteams/settings.json). Missing role keys inherit the defaults.
export interface RoleSettings {
  default_model: string
  default_effort: string | null
  models: Record<string, string>
  efforts: Record<string, string>
  plugins?: PluginRow[] // F-23
  [k: string]: unknown
}

// F-23: the one plugin list for all roles (`name@marketplace` ids as `enabledPlugins` uses them). `ask` = roles must
// get the Master's approval before using a skill of that plugin. `status` is what the last turns found: `missing`
// = switched on but the turn did not load it (the daemon keeps it in memory, last turn wins).
export interface PluginRow {
  id: string
  on: boolean
  ask: boolean
}
export interface PluginStatus {
  missing: boolean
  checked_at: number
  role: string
}
export interface PluginSettings {
  rows: PluginRow[]
  status: Record<string, PluginStatus>
}

export interface SettingsOptions {
  roles: string[]
  models: { id: string; label: string }[] // ids go to --model; aliases first as "X (latest)"
  efforts: string[]
  effort_support: Record<string, string[]>
}

// F-29: the raw bytes of a mock. The daemon sends it with a CSP `sandbox`, and the UI frames html in a sandboxed iframe.
export const fileUrl = (pid: string, fid: number, name: string) => `/api/p/${pid}/features/${fid}/files/${encodeURIComponent(name)}`

export const api = {
  settings: () => req<RoleSettings>('/api/settings'),
  saveSettings: (body: Partial<RoleSettings>) => put<RoleSettings>('/api/settings', body),
  settingsOptions: () => req<SettingsOptions>('/api/settings/options'),
  pluginSettings: () => req<PluginSettings>('/api/settings/plugins'),
  health: () => req<Health>('/api/health'),
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

  // M1b: features, docs, approvals, commits, locks, inbox.
  features: (pid: string, history = false) =>
    req<Feature[]>(`/api/p/${pid}/features?history=${history ? 1 : 0}`),
  createFeature: (pid: string, body: { text: string; starter: Role | string; title?: string }) =>
    post<Feature>(`/api/p/${pid}/features`, body),
  patchFeature: (
    pid: string,
    id: number,
    body: { phase?: string; owner?: string | null; lead?: string; title?: string; archived?: boolean },
  ) => patch<Feature>(`/api/p/${pid}/features/${id}`, body),
  featureDocs: (pid: string, id: number) => req<FeatureDoc[]>(`/api/p/${pid}/features/${id}/docs`),
  featureDoc: (pid: string, id: number, name: string) =>
    req<{ name: string; text: string }>(`/api/p/${pid}/features/${id}/docs/${encodeURIComponent(name)}`),
  approvals: (pid: string, state: ApprovalState | 'all' = 'pending') =>
    req<Approval[]>(`/api/p/${pid}/approvals?state=${state}`),
  decideApproval: (
    pid: string,
    id: number,
    body: { decision: ApprovalDecision; note?: string; set_phase?: string },
  ) => post<Approval>(`/api/p/${pid}/approvals/${id}/decide`, body),
  commits: (pid: string, state: CommitState | 'all' = 'master_review', feature?: number) => {
    const q = new URLSearchParams({ state })
    if (feature != null) q.set('feature', String(feature))
    return req<Commit[]>(`/api/p/${pid}/commits?${q}`)
  },
  commitDiff: (pid: string, id: number) => req<CommitDiff>(`/api/p/${pid}/commits/${id}/diff`),
  decideCommit: (
    pid: string,
    id: number,
    body: { decision: 'approve' | 'reject'; message?: string; note?: string; force?: boolean; group?: boolean },
  ) => post<Commit & { group?: Commit[] }>(`/api/p/${pid}/commits/${id}/decide`, body),
  locks: (pid: string) => req<Lock[]>(`/api/p/${pid}/locks`),
  releaseLock: (pid: string, path: string) =>
    del<unknown>(`/api/p/${pid}/locks?path=${encodeURIComponent(path)}`),
  inbox: (pid: string) => req<Inbox>(`/api/p/${pid}/inbox`),
  resolveInbox: (pid: string, message_id: number) => post<unknown>(`/api/p/${pid}/inbox/resolve`, { message_id }),

  // M1c: dashboard, stop / fresh start, standups, handoffs, history, activity.
  dashboard: (pid: string) => req<Dashboard>(`/api/p/${pid}/dashboard`),
  stopRole: (pid: string, role: string) => post<unknown>(`/api/p/${pid}/team/${role}/stop`, {}),
  freshStartRole: (pid: string, role: string, handoff: boolean) =>
    post<unknown>(`/api/p/${pid}/team/${role}/fresh-start`, { handoff }),
  startStandup: (pid: string) => post<Standup | { status: 'nothing_changed' }>(`/api/p/${pid}/standup`, {}),
  standups: (pid: string) => req<Standup[]>(`/api/p/${pid}/standups`),
  standupReport: (pid: string, id: number) => req<{ text: string }>(`/api/p/${pid}/standups/${id}/report`),
  handoffs: (pid: string, role?: string) =>
    req<Handoff[]>(`/api/p/${pid}/handoffs${role ? `?role=${encodeURIComponent(role)}` : ''}`),
  freshStartFeature: (pid: string, fid: number, mode: 'keep_docs' | 'start_over') =>
    post<unknown>(`/api/p/${pid}/features/${fid}/fresh-start`, { mode }),
  startBrainstorm: (pid: string, fid: number, topic: string) =>
    post<Brainstorm>(`/api/p/${pid}/features/${fid}/brainstorm`, { topic }),
  brainstorms: (pid: string, fid: number) => req<Brainstorm[]>(`/api/p/${pid}/features/${fid}/brainstorms`),
  featuresHistory: (pid: string) => req<Feature[]>(`/api/p/${pid}/features/history`),
  activity: (pid: string, limit = 30) => req<Activity[]>(`/api/p/${pid}/activity?limit=${limit}`),
}

// Stale file list from a failed commit approve (409), or null for any other error.
export function staleFrom(e: unknown): string[] | null {
  if (e instanceof ApiError && e.status === 409 && Array.isArray(e.detail?.stale)) return e.detail.stale as string[]
  return null
}

// F-11: a group commit's 409 names stale files per member ({"<id>": [files]}); null for any other error.
export function groupStaleFrom(e: unknown): Record<string, string[]> | null {
  const s = e instanceof ApiError && e.status === 409 ? e.detail?.stale : null
  return s && typeof s === 'object' && !Array.isArray(s) ? (s as Record<string, string[]>) : null
}

export function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export function fmtAgo(ts: number | null | undefined): string {
  if (!ts) return ''
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 45) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

export function fmtDuration(secs: number): string {
  if (secs < 60) return `${Math.round(secs)}s`
  const m = Math.floor(secs / 60)
  const s = Math.round(secs % 60)
  return `${m}m ${String(s).padStart(2, '0')}s`
}
