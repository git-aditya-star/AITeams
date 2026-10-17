import { createSlice, type PayloadAction } from '@reduxjs/toolkit'
import type { Conversation, Dashboard, Feature, Health, Inbox, Lock, Message, Project, ProjectSummary, Question, RateLimitInfo, Reaction, ReceiptState, Standup, TeamRole } from '../api'
import * as ev from './actions'

const inboxTotal = (i: Inbox) => i.questions.length + i.approvals.length + i.commits.length + i.messages.length
const standupActive = (s: Standup | null | undefined) => !!s && (s.state === 'collecting' || s.state === 'compiling')
// Claude Code reports allowed | allowed_warning | rejected; only a non-allowed status holds turns.
const limited = (info: RateLimitInfo | null | undefined) => (info && !info.status.startsWith('allowed') ? info : null)

const upsert = <T extends { id: number | string }>(list: T[], row: T, at: 'start' | 'end' = 'end') => {
  const i = list.findIndex((x) => x.id === row.id)
  if (i >= 0) list[i] = { ...list[i], ...row }
  else if (at === 'start') list.unshift(row)
  else list.push(row)
}

export const session = createSlice({
  name: 'session',
  initialState: {
    pid: '',
    projects: [] as ProjectSummary[],
    project: null as Project | null,
    paused: false,
    rateLimit: null as RateLimitInfo | null,
    health: null as Health | null,
    error: null as string | null,
  },
  reducers: {
    projectsLoaded(s, a: PayloadAction<ProjectSummary[]>) {
      s.projects = a.payload
    },
    projectLoaded(s, a: PayloadAction<Project>) {
      s.project = a.payload
      s.paused = a.payload.paused
      s.rateLimit = limited(a.payload.rate_limited)
    },
    healthLoaded(s, a: PayloadAction<Health>) {
      s.health = a.payload
    },
    errorSet(s, a: PayloadAction<string | null>) {
      s.error = a.payload
    },
  },
  extraReducers: (b) =>
    b
      .addCase(ev.projectOpened, (s, a) => {
        s.pid = a.payload
        s.project = null
        s.error = null
      })
      .addCase(ev.rateLimitReceived, (s, a) => {
        s.rateLimit = limited(a.payload)
      })
      .addCase(ev.pausedReceived, (s, a) => {
        s.paused = a.payload
      }),
})

const featureConversation = (f: Feature): Conversation => ({
  id: f.conversation_id,
  kind: 'feature',
  title: `${f.key} ${f.title}`,
  role: null,
  created_at: f.created_at,
  last_message_at: f.last_activity,
})

export const conversations = createSlice({
  name: 'conversations',
  initialState: [] as Conversation[],
  reducers: { loaded: (_s, a: PayloadAction<Conversation[]>) => a.payload },
  extraReducers: (b) =>
    b
      .addCase(ev.projectOpened, () => [])
      .addCase(ev.messageReceived, (s, a) => {
        const c = s.find((x) => x.id === a.payload.conversation_id)
        if (c) c.last_message_at = a.payload.created_at
      })
      .addCase(ev.conversationUpserted, (s, a) => upsert(s, a.payload, 'start'))
      .addCase(ev.featureUpserted, (s, a) => {
        const f = a.payload
        const c = s.find((x) => x.id === f.conversation_id)
        if (c) c.title = `${f.key} ${f.title}`
        else s.unshift(featureConversation(f))
      }),
})

export const features = createSlice({
  name: 'features',
  initialState: [] as Feature[],
  reducers: { loaded: (_s, a: PayloadAction<Feature[]>) => a.payload },
  extraReducers: (b) =>
    b
      .addCase(ev.projectOpened, () => [])
      .addCase(ev.featureUpserted, (s, a) => upsert(s, a.payload, 'start'))
      .addCase(ev.messageReceived, (s, a) => {
        const f = s.find((x) => x.conversation_id === a.payload.conversation_id)
        if (f) f.last_activity = a.payload.created_at
      }),
})

type Page = { conversation: string; items: Message[]; hasMore: boolean }

// Per-message updates that arrive while a page request is in flight; replayed on top of the page, which was read
// before them (otherwise `loaded` would write the old receipts/reactions back).
type PendingOp =
  | { kind: 'receipt'; message_id: number; role: string; state: ReceiptState }
  | { kind: 'reactions'; message_id: number; reactions: Reaction[] }

type MessagesState = { conversation: string | null; items: Message[]; hasMore: boolean; loading: boolean; pending: PendingOp[] }
const emptyMessages: MessagesState = { conversation: null, items: [], hasMore: false, loading: false, pending: [] }

const applyOp = (items: Message[], op: PendingOp) => {
  const m = items.find((x) => x.id === op.message_id)
  if (!m) return
  if (op.kind === 'receipt') m.receipts = [...(m.receipts || []).filter((r) => r.role !== op.role), { role: op.role, state: op.state }]
  else m.reactions = op.reactions
}

const track = (s: MessagesState, op: PendingOp) => {
  applyOp(s.items, op)
  if (s.loading) s.pending.push(op)
}

// The open conversation's messages; `conversation` guards against a late response for one the Master already left.
export const messages = createSlice({
  name: 'messages',
  initialState: emptyMessages,
  reducers: {
    cleared(s, a: PayloadAction<string>) {
      s.conversation = a.payload
      s.items = []
      s.hasMore = false
      s.loading = false
      s.pending = []
    },
    // The latest page is being fetched: from here on, per-message updates are kept for replay.
    loadStarted(s, a: PayloadAction<string>) {
      if (a.payload !== s.conversation) return
      s.loading = true
      s.pending = []
    },
    // The page wins for what it contains; messages that arrived over WS after it was read (ids above its newest)
    // are kept, and updates received meanwhile are replayed on top.
    loaded(s, a: PayloadAction<Page>) {
      if (a.payload.conversation !== s.conversation) return
      const page = a.payload.items
      const top = page.reduce((n, m) => Math.max(n, m.id), 0)
      const ids = new Set(page.map((m) => m.id))
      const live = s.items.filter((m) => m.id > top && !ids.has(m.id))
      s.items = [...page, ...live].sort((x, y) => x.id - y.id)
      for (const op of s.pending) applyOp(s.items, op)
      s.hasMore = a.payload.hasMore
      s.loading = false
      s.pending = []
    },
    loadFailed(s, a: PayloadAction<string>) {
      if (a.payload !== s.conversation) return
      s.loading = false
      s.pending = []
    },
    olderLoaded(s, a: PayloadAction<Page>) {
      if (a.payload.conversation !== s.conversation) return
      s.items = [...a.payload.items, ...s.items]
      s.hasMore = a.payload.hasMore
    },
  },
  extraReducers: (b) =>
    b
      .addCase(ev.projectOpened, () => emptyMessages)
      .addCase(ev.messageReceived, (s, a) => {
        if (a.payload.conversation_id === s.conversation) upsert(s.items, a.payload)
      })
      .addCase(ev.receiptReceived, (s, a) => track(s, { kind: 'receipt', ...a.payload }))
      .addCase(ev.reactionsReceived, (s, a) => track(s, { kind: 'reactions', ...a.payload }))
      .addCase(ev.approvalUpdated, (s, a) => {
        for (const m of s.items)
          if (m.ref === `approval:${a.payload.id}` || m.approval?.id === a.payload.id) {
            m.ref = m.ref || `approval:${a.payload.id}`
            m.approval = a.payload
          }
      })
      .addCase(ev.commitUpdated, (s, a) => {
        for (const m of s.items)
          if (m.ref === `commit:${a.payload.id}` || m.commit?.id === a.payload.id) {
            m.ref = m.ref || `commit:${a.payload.id}`
            m.commit = a.payload
          }
      })
      // F-10: a card's message carries its question; answer/withdraw frames close it in place.
      .addCase(ev.questionUpdated, (s, a) => {
        for (const m of s.items) if (m.ref === `question:${a.payload.id}`) m.question = a.payload
      }),
})

// `count` is the badge from WS `inbox` frames; `data` is the last full fetch, whose own count wins once loaded.
export const inbox = createSlice({
  name: 'inbox',
  initialState: { data: null as Inbox | null, count: 0 },
  reducers: {
    loaded(s, a: PayloadAction<Inbox>) {
      s.data = a.payload
      s.count = a.payload.count ?? inboxTotal(a.payload)
    },
    messageResolved(s, a: PayloadAction<number>) {
      if (!s.data) return
      s.data.messages = s.data.messages.filter((x) => x.id !== a.payload)
      s.data.count = inboxTotal(s.data)
    },
  },
  extraReducers: (b) =>
    b
      .addCase(ev.projectOpened, () => ({ data: null, count: 0 }))
      .addCase(ev.inboxCountReceived, (s, a) => {
        s.count = a.payload
      })
      .addCase(ev.approvalUpdated, (s, a) => {
        if (!s.data) return
        const rest = s.data.approvals.filter((x) => x.id !== a.payload.id)
        s.data.approvals = a.payload.state === 'pending' ? [...rest, a.payload].sort((x, y) => x.created_at - y.created_at) : rest
        s.data.count = inboxTotal(s.data)
      })
      .addCase(ev.commitUpdated, (s, a) => {
        if (!s.data) return
        const rest = s.data.commits.filter((x) => x.id !== a.payload.id)
        s.data.commits = a.payload.state === 'master_review' ? [...rest, a.payload].sort((x, y) => x.created_at - y.created_at) : rest
        s.data.count = inboxTotal(s.data)
      })
      .addCase(ev.questionUpdated, (s, a) => {
        if (!s.data) return
        const rest = s.data.questions.filter((x) => x.id !== a.payload.id)
        s.data.questions = a.payload.state === 'pending' ? [...rest, a.payload] : rest
        s.data.count = inboxTotal(s.data)
      }),
})

export const team = createSlice({
  name: 'team',
  initialState: [] as TeamRole[],
  reducers: { loaded: (_s, a: PayloadAction<TeamRole[]>) => a.payload },
  extraReducers: (b) =>
    b
      .addCase(ev.projectOpened, () => [])
      .addCase(ev.teamReceived, (_s, a) => a.payload)
      .addCase(ev.turnStateReceived, (s, a) => {
        // Optimistic dot update; the authoritative numbers come from /team.
        const t = a.payload
        const r = s.find((x) => x.role === t.role)
        if (!r) return
        r.state = t.state === 'running' ? 'running' : t.state === 'queued' ? 'queued' : 'idle'
        if (t.state === 'running') r.kind = t.kind
      }),
})

export const dashboard = createSlice({
  name: 'dashboard',
  initialState: null as Dashboard | null,
  reducers: {
    loaded: (_s, a: PayloadAction<Dashboard>) => a.payload,
    standupStarted(s, a: PayloadAction<Standup>) {
      if (s) s.standup = a.payload
    },
  },
  extraReducers: (b) =>
    b
      .addCase(ev.projectOpened, () => null)
      .addCase(ev.teamReceived, (s, a) => {
        if (s) s.team = a.payload
      })
      .addCase(ev.activityReceived, (s, a) => {
        if (s && !s.activity.some((x) => x.id === a.payload.id)) s.activity = [a.payload, ...s.activity].slice(0, 30)
      })
      .addCase(ev.standupUpdated, (s, a) => {
        if (!s) return
        const su = a.payload
        if (standupActive(su)) s.standup = su
        else {
          if (s.standup?.id === su.id) s.standup = null
          if (su.state === 'done' || !s.last_standup) s.last_standup = su
        }
      }),
})

export const questions = createSlice({
  name: 'questions',
  initialState: [] as Question[],
  reducers: {
    loaded: (_s, a: PayloadAction<Question[]>) => a.payload,
    // After a reconnect: keep the answered/expired ones already shown, take the pending list fresh.
    pendingMerged: (s, a: PayloadAction<Question[]>) => [...s.filter((q) => q.state !== 'pending'), ...a.payload],
  },
  extraReducers: (b) =>
    b.addCase(ev.projectOpened, () => []).addCase(ev.questionUpdated, (s, a) => {
      const i = s.findIndex((x) => x.id === a.payload.id)
      if (i >= 0) s[i] = a.payload
      else s.push(a.payload)
    }),
})

export const locks = createSlice({
  name: 'locks',
  initialState: [] as Lock[],
  reducers: {
    loaded: (_s, a: PayloadAction<Lock[]>) => a.payload,
    released: (s, a: PayloadAction<string>) => s.filter((l) => l.path !== a.payload),
  },
  extraReducers: (b) => b.addCase(ev.projectOpened, () => []).addCase(ev.locksReceived, (_s, a) => a.payload),
})
