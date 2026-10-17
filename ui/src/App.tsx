import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import type { Activity, Approval, Commit, Conversation, Dashboard, Feature, Health, Inbox, InboxMessage, Lock, Message, Project, ProjectSummary, Question, RateLimitInfo, Standup, TeamRole, Turn } from './api'
import { api } from './api'
import { roleName } from './roles'
import { useProjectWs, type WsFrame } from './ws'
import Sidebar from './components/Sidebar'
import Banners from './components/Banners'
import TeamStrip from './components/TeamStrip'
import MessageList from './components/MessageList'
import ChatBox from './components/ChatBox'
import QuestionCard from './components/QuestionCard'
import LiveSession from './components/LiveSession'
import FeatureHeader from './components/FeatureHeader'
import FeatureDocs from './components/FeatureDocs'
import InboxView from './components/InboxView'
import DashboardView from './components/DashboardView'
import HistoryView from './components/HistoryView'
import RoleActions from './components/RoleActions'
import StandupButton from './components/StandupButton'
import { CardContext } from './components/CardContext'

const PAGE = 50
type Tab = 'chat' | 'live' | 'docs'
const VIEWS = ['dashboard', 'inbox', 'history']

const featureConversation = (f: Feature): Conversation => ({
  id: f.conversation_id,
  kind: 'feature',
  title: `${f.key} ${f.title}`,
  role: null,
  created_at: f.created_at,
  last_message_at: f.last_activity,
})

const inboxTotal = (i: Inbox) => i.questions.length + i.approvals.length + i.commits.length + i.messages.length
const standupActive = (s: Standup | null | undefined) => !!s && (s.state === 'collecting' || s.state === 'compiling')

export default function App() {
  const { pid = '' } = useParams()
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const current = params.get('c') || 'dashboard'
  const tabParam = params.get('tab')
  const tab: Tab = tabParam === 'live' ? 'live' : tabParam === 'docs' ? 'docs' : 'chat'
  const highlight = Number(params.get('m')) || null

  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [project, setProject] = useState<Project | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [features, setFeatures] = useState<Feature[]>([])
  const [locks, setLocks] = useState<Lock[]>([])
  const [inbox, setInbox] = useState<Inbox | null>(null)
  const [inboxCount, setInboxCount] = useState(0)
  const [team, setTeam] = useState<TeamRole[]>([])
  const [dash, setDash] = useState<Dashboard | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [questions, setQuestions] = useState<Question[]>([])
  const [paused, setPaused] = useState(false)
  const [rateLimit, setRateLimit] = useState<RateLimitInfo | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Fan-out for components (LiveSession) that want raw WS frames.
  const listeners = useRef(new Set<(f: WsFrame) => void>())
  const subscribe = useCallback((fn: (f: WsFrame) => void) => {
    listeners.current.add(fn)
    return () => { listeners.current.delete(fn) }
  }, [])

  const select = useCallback(
    (id: string, t?: Tab, m?: number) => {
      const p = new URLSearchParams()
      if (id !== 'dashboard') p.set('c', id)
      if (t && t !== 'chat') p.set('tab', t)
      if (m) p.set('m', String(m))
      setParams(p)
    },
    [setParams],
  )

  const refreshTeam = useCallback(() => api.team(pid).then((t) => setTeam(t.roles)).catch(() => {}), [pid])
  // Version/upgrade banner: read once per page load and on WS reconnect only (no timer).
  const refreshHealth = useCallback(() => api.health().then(setHealth).catch(() => {}), [])
  const refreshDashboard = useCallback(() => api.dashboard(pid).then(setDash).catch(() => {}), [pid])
  const refreshConversations = useCallback(() => api.conversations(pid).then(setConversations).catch(() => {}), [pid])
  const refreshFeatures = useCallback(() => api.features(pid, true).then(setFeatures).catch(() => {}), [pid])
  const refreshLocks = useCallback(() => api.locks(pid).then(setLocks).catch(() => {}), [pid])
  const refreshInbox = useCallback(
    () =>
      api
        .inbox(pid)
        .then((i) => {
          setInbox(i)
          setInboxCount(i.count ?? inboxTotal(i))
        })
        .catch(() => {}),
    [pid],
  )
  const loadMessages = useCallback(
    (conv: string) =>
      api.messages(pid, conv, undefined, PAGE).then((ms) => {
        setMessages(ms)
        setHasMore(ms.length >= PAGE)
      }),
    [pid],
  )

  const upsertFeature = useCallback((f: Feature) => {
    setFeatures((fs) => (fs.some((x) => x.id === f.id) ? fs.map((x) => (x.id === f.id ? { ...x, ...f } : x)) : [f, ...fs]))
    setConversations((cs) => (cs.some((c) => c.id === f.conversation_id) ? cs.map((c) => (c.id === f.conversation_id ? { ...c, title: `${f.key} ${f.title}` } : c)) : [featureConversation(f), ...cs]))
  }, [])

  // Push an updated approval row into the message list and the inbox.
  const applyApproval = useCallback((a: Approval) => {
    setMessages((ms) => ms.map((m) => (m.ref === `approval:${a.id}` || (m.approval && m.approval.id === a.id) ? { ...m, ref: m.ref || `approval:${a.id}`, approval: a } : m)))
    setInbox((i) => {
      if (!i) return i
      const rest = i.approvals.filter((x) => x.id !== a.id)
      const approvals = a.state === 'pending' ? [...rest, a].sort((x, y) => x.created_at - y.created_at) : rest
      const next = { ...i, approvals }
      return { ...next, count: inboxTotal(next) }
    })
  }, [])
  const applyCommit = useCallback((c: Commit) => {
    setMessages((ms) => ms.map((m) => (m.ref === `commit:${c.id}` || (m.commit && m.commit.id === c.id) ? { ...m, ref: m.ref || `commit:${c.id}`, commit: c } : m)))
    setInbox((i) => {
      if (!i) return i
      const rest = i.commits.filter((x) => x.id !== c.id)
      const commits = c.state === 'master_review' ? [...rest, c].sort((x, y) => x.created_at - y.created_at) : rest
      const next = { ...i, commits }
      return { ...next, count: inboxTotal(next) }
    })
  }, [])

  // Initial load per project.
  useEffect(() => {
    if (!pid) return
    setError(null)
    setDash(null)
    api.projects().then(setProjects).catch(() => {})
    refreshHealth()
    api
      .project(pid)
      .then((p) => {
        setProject(p)
        setPaused(p.paused)
        setRateLimit(p.rate_limited && !p.rate_limited.status.startsWith('allowed') ? p.rate_limited : null)
      })
      .catch((e) => setError(e.message))
    refreshConversations()
    refreshTeam()
    refreshFeatures()
    refreshLocks()
    refreshInbox()
    refreshDashboard()
    api.questions(pid, 'pending').then(setQuestions).catch(() => {})
  }, [pid, refreshConversations, refreshTeam, refreshFeatures, refreshLocks, refreshInbox, refreshDashboard, refreshHealth])

  useEffect(() => {
    if (!pid) return
    const id = setInterval(refreshTeam, 30000)
    return () => clearInterval(id)
  }, [pid, refreshTeam])

  useEffect(() => {
    if (!pid) return
    setMessages([])
    if (current === 'inbox') refreshInbox()
    else if (current === 'dashboard') refreshDashboard()
    if (VIEWS.includes(current)) return
    loadMessages(current).catch((e) => setError(e.message))
  }, [pid, current, loadMessages, refreshInbox, refreshDashboard])

  const loadMore = () => {
    const first = messages[0]
    if (!first) return
    api.messages(pid, current, first.id, PAGE).then((older) => {
      setMessages((ms) => [...older, ...ms])
      setHasMore(older.length >= PAGE)
    })
  }

  const onFrame = (f: WsFrame) => {
    listeners.current.forEach((fn) => fn(f))
    // waits_on / stale / counts only come from /dashboard, so refetch it while it is on screen.
    const touchDash = () => { if (current === 'dashboard') refreshDashboard() }
    switch (f.event) {
      case 'message': {
        const m = f.data as Message
        if (m.conversation_id === current) setMessages((ms) => (ms.some((x) => x.id === m.id) ? ms.map((x) => (x.id === m.id ? { ...x, ...m } : x)) : [...ms, m]))
        setConversations((cs) => cs.map((c) => (c.id === m.conversation_id ? { ...c, last_message_at: m.created_at } : c)))
        setFeatures((fs) => fs.map((x) => (x.conversation_id === m.conversation_id ? { ...x, last_activity: m.created_at } : x)))
        break
      }
      case 'receipt': {
        const { message_id, role, state } = f.data
        setMessages((ms) =>
          ms.map((m) => {
            if (m.id !== message_id) return m
            const rs = (m.receipts || []).filter((r) => r.role !== role)
            return { ...m, receipts: [...rs, { role, state }] }
          }),
        )
        break
      }
      case 'turn_state': {
        const t = f.data as Turn
        // Optimistic dot update; the authoritative numbers come from /team.
        setTeam((ts) => ts.map((r) => (r.role === t.role ? { ...r, state: t.state === 'running' ? 'running' : t.state === 'queued' ? 'queued' : 'idle', kind: t.state === 'running' ? t.kind : r.kind } : r)))
        refreshTeam()
        break
      }
      case 'team': {
        const list = Array.isArray(f.data) ? f.data : f.data?.roles
        if (Array.isArray(list)) {
          setTeam(list)
          setDash((d) => (d ? { ...d, team: list } : d))
        }
        break
      }
      case 'activity': {
        const a = f.data as Activity
        setDash((d) => (d && !d.activity.some((x) => x.id === a.id) ? { ...d, activity: [a, ...d.activity].slice(0, 30) } : d))
        break
      }
      case 'standup': {
        const s = f.data as Standup
        setDash((d) => {
          if (!d) return d
          if (standupActive(s)) return { ...d, standup: s }
          return { ...d, standup: d.standup?.id === s.id ? null : d.standup, last_standup: s.state === 'done' || !d.last_standup ? s : d.last_standup }
        })
        break
      }
      case 'conversation': {
        const c = f.data as Conversation
        setConversations((cs) => (cs.some((x) => x.id === c.id) ? cs.map((x) => (x.id === c.id ? { ...x, ...c } : x)) : [c, ...cs]))
        break
      }
      case 'question': {
        const q = f.data as Question
        setQuestions((qs) => (qs.some((x) => x.id === q.id) ? qs.map((x) => (x.id === q.id ? q : x)) : [...qs, q]))
        setInbox((i) => {
          if (!i) return i
          const rest = i.questions.filter((x) => x.id !== q.id)
          const next = { ...i, questions: q.state === 'pending' ? [...rest, q] : rest }
          return { ...next, count: inboxTotal(next) }
        })
        touchDash()
        break
      }
      case 'feature':
        upsertFeature(f.data as Feature)
        touchDash()
        break
      case 'approval':
        applyApproval(f.data as Approval)
        touchDash()
        break
      case 'commit':
        applyCommit(f.data as Commit)
        touchDash()
        break
      case 'locks':
        setLocks(Array.isArray(f.data?.locks) ? f.data.locks : [])
        break
      case 'inbox':
        // Count lands immediately; the full inbox is refetched so the view and badge stay exact.
        if (typeof f.data?.count === 'number') setInboxCount(f.data.count)
        refreshInbox()
        touchDash()
        break
      case 'rate_limit': {
        const info = f.data?.info as RateLimitInfo | undefined
        setRateLimit(info && !info.status.startsWith('allowed') ? info : null)
        break
      }
      case 'paused':
        setPaused(!!f.data?.paused)
        break
    }
  }
  const onReconnect = () => {
    if (!VIEWS.includes(current)) loadMessages(current).catch(() => {})
    refreshHealth()
    refreshTeam()
    refreshConversations()
    refreshFeatures()
    refreshLocks()
    refreshInbox()
    refreshDashboard()
    api.questions(pid, 'pending').then((qs) => setQuestions((old) => [...old.filter((q) => q.state !== 'pending'), ...qs])).catch(() => {})
  }
  useProjectWs(pid, onFrame, onReconnect)

  const conv = conversations.find((c) => c.id === current)
  const feature = features.find((f) => f.conversation_id === current)
  const dmRole = current.startsWith('dm-') ? current.slice(3) : null
  const dmTeam = dmRole ? team.find((t) => t.role === dmRole) : undefined
  const title =
    current === 'inbox'
      ? 'Inbox'
      : current === 'dashboard'
        ? 'Dashboard'
        : current === 'history'
          ? 'History'
          : feature
            ? `${feature.key} ${feature.title}`
            : conv
              ? conv.kind === 'channel'
                ? `# ${conv.title}`
                : conv.kind === 'dm'
                  ? roleName(conv.role || dmRole || '')
                  : conv.title
              : dmRole
                ? roleName(dmRole)
                : current === 'general' || current === 'standup'
                  ? `# ${current}`
                  : current
  const pending = useMemo(() => questions.filter((q) => q.state === 'pending'), [questions])
  const dmQuestions = useMemo(() => {
    if (!dmRole) return []
    const mine = questions.filter((q) => q.role === dmRole)
    const done = mine.filter((q) => q.state !== 'pending').slice(-3)
    return [...done, ...mine.filter((q) => q.state === 'pending')]
  }, [questions, dmRole])

  const answer = (q: Question) => async (answers: Record<string, string>) => {
    const updated = await api.answerQuestion(pid, q.id, answers)
    setQuestions((qs) => qs.map((x) => (x.id === q.id ? updated : x)))
    setInbox((i) => {
      if (!i) return i
      const next = { ...i, questions: i.questions.filter((x) => x.id !== q.id) }
      return { ...next, count: inboxTotal(next) }
    })
  }

  const resolveInbox = async (m: InboxMessage) => {
    await api.resolveInbox(pid, m.id)
    setInbox((i) => {
      if (!i) return i
      const next = { ...i, messages: i.messages.filter((x) => x.id !== m.id) }
      return { ...next, count: inboxTotal(next) }
    })
  }

  const patchFeatureById = async (id: number, patch: { phase?: string; owner?: string | null; archived?: boolean }) => {
    try {
      const updated = await api.patchFeature(pid, id, patch)
      upsertFeature(updated)
    } catch (e: any) {
      setError(e?.message || 'patch failed')
      throw e
    }
  }
  const patchFeature = (patch: { phase?: string; owner?: string | null; archived?: boolean }) => (feature ? patchFeatureById(feature.id, patch) : Promise.resolve())

  const freshStartFeature = async (mode: 'keep_docs' | 'start_over') => {
    if (!feature) return
    try {
      await api.freshStartFeature(pid, feature.id, mode)
      refreshConversations()
      refreshFeatures()
      await loadMessages(current)
    } catch (e: any) {
      setError(e?.message || 'fresh start failed')
      throw e
    }
  }

  const stopRole = async (role: string) => {
    await api.stopRole(pid, role)
    refreshTeam()
  }
  const freshStartRole = async (role: string, handoff: boolean) => {
    await api.freshStartRole(pid, role, handoff)
    refreshTeam()
  }
  const startStandup = async () => {
    const r = await api.startStandup(pid)
    if (!('status' in r)) setDash((d) => (d ? { ...d, standup: r } : d))
    return r
  }

  const releaseLock = async (path: string) => {
    try {
      await api.releaseLock(pid, path)
      setLocks((ls) => ls.filter((l) => l.path !== path))
    } catch (e: any) {
      setError(e?.message || 'release failed')
    }
  }

  const newTask = async (body: { text: string; starter: string }) => {
    const f = await api.createFeature(pid, body)
    upsertFeature(f)
    select(f.conversation_id)
  }

  const badgeCount = inbox ? inbox.count : inboxCount
  const cardActions = useMemo(() => ({ pid, features, onApproval: applyApproval, onCommit: applyCommit }), [pid, features, applyApproval, applyCommit])

  return (
    <CardContext.Provider value={cardActions}>
      <div className="h-full flex">
        <Sidebar
          projects={projects}
          project={project}
          features={features}
          team={team}
          current={current}
          paused={paused}
          inboxCount={badgeCount}
          health={health}
          onPickProject={(id) => nav(`/p/${id}`)}
          onSelect={(id) => select(id)}
          onNewTask={newTask}
          onPause={(p) => {
            setPaused(p)
            api.pause(pid, p).then((r) => setPaused(r.paused)).catch(() => setPaused(!p))
          }}
        />
        <main className="flex-1 flex flex-col min-w-0 h-full">
          <Banners
            rateLimit={rateLimit}
            paused={paused}
            pending={pending}
            upgradeAvailable={!!health?.source_head && !!health?.sha && health.source_head !== health.sha}
            schemaAhead={!!project?.schema_ahead}
            onAnswer={(q) => select(`dm-${q.role}`)}
          />
          <TeamStrip team={team} onSelect={(r) => select(`dm-${r}`)} />
          {feature ? (
            <FeatureHeader
              feature={feature}
              locks={locks}
              tab={tab === 'docs' ? 'docs' : 'chat'}
              error={error}
              onTab={(t) => select(current, t)}
              onPatch={patchFeature}
              onReleaseLock={releaseLock}
              onFreshStart={freshStartFeature}
            />
          ) : (
            <div className="px-4 py-2 border-b border-gray-200 flex items-center gap-4 shrink-0 flex-wrap">
              <h1 className="font-semibold text-[15px] truncate">{title}</h1>
              {dmRole && (
                <div className="flex gap-1 text-[13px]">
                  {(['chat', 'live'] as const).map((t) => (
                    <button
                      key={t}
                      onClick={() => select(current, t)}
                      className={`px-2 py-0.5 rounded-md ${tab === t ? 'bg-gray-100 font-medium text-gray-900' : 'text-gray-500 hover:text-gray-800'}`}
                    >
                      {t === 'chat' ? 'Chat' : 'Live session'}
                    </button>
                  ))}
                </div>
              )}
              {dmRole && <RoleActions t={dmTeam} onStop={() => stopRole(dmRole)} onFreshStart={(h) => freshStartRole(dmRole, h)} />}
              {current === 'standup' && <StandupButton standup={dash?.standup ?? null} onStart={startStandup} />}
              {error && <span className="ml-auto text-xs text-red-600 truncate">{error}</span>}
            </div>
          )}

          {current === 'dashboard' ? (
            <DashboardView
              dash={dash}
              team={team}
              onSelect={(id, m) => select(id, undefined, m)}
              onNewTask={newTask}
              onStop={stopRole}
              onFreshStart={freshStartRole}
              onStandup={startStandup}
            />
          ) : current === 'inbox' ? (
            <InboxView inbox={inbox} onAnswer={answer} onOpen={(id) => select(id)} onResolve={resolveInbox} />
          ) : current === 'history' ? (
            <HistoryView pid={pid} onOpen={(id) => select(id)} onPatch={patchFeatureById} />
          ) : feature && tab === 'docs' ? (
            <FeatureDocs pid={pid} featureId={feature.id} refreshKey={messages.length} />
          ) : dmRole && tab === 'live' ? (
            <LiveSession pid={pid} role={dmRole} subscribe={subscribe} />
          ) : (
            <>
              <MessageList
                messages={messages}
                hasMore={hasMore}
                onLoadMore={loadMore}
                resetAfter={conv?.reset_after || 0}
                highlight={highlight}
                footer={dmQuestions.length ? dmQuestions.map((q) => <QuestionCard key={`${q.id}-${q.state}`} q={q} onAnswer={answer(q)} />) : undefined}
              />
              <ChatBox
                placeholder={dmRole ? `Message ${roleName(dmRole)}` : `Message ${title}`}
                onSend={async (text) => {
                  await api.sendMessage(pid, current, text)
                }}
              />
            </>
          )}
        </main>
      </div>
    </CardContext.Provider>
  )
}
