import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import type { Conversation, Message, Project, ProjectSummary, Question, RateLimitInfo, TeamRole, Turn } from './api'
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

const PAGE = 50

export default function App() {
  const { pid = '' } = useParams()
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const current = params.get('c') || 'general'
  const tab = params.get('tab') === 'live' ? 'live' : 'chat'

  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [project, setProject] = useState<Project | null>(null)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [team, setTeam] = useState<TeamRole[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [questions, setQuestions] = useState<Question[]>([])
  const [paused, setPaused] = useState(false)
  const [rateLimit, setRateLimit] = useState<RateLimitInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Fan-out for components (LiveSession) that want raw WS frames.
  const listeners = useRef(new Set<(f: WsFrame) => void>())
  const subscribe = useCallback((fn: (f: WsFrame) => void) => {
    listeners.current.add(fn)
    return () => { listeners.current.delete(fn) }
  }, [])

  const select = useCallback(
    (id: string, t?: 'chat' | 'live') => {
      const p = new URLSearchParams()
      if (id !== 'general') p.set('c', id)
      if (t === 'live') p.set('tab', 'live')
      setParams(p)
    },
    [setParams],
  )

  const refreshTeam = useCallback(() => api.team(pid).then((t) => setTeam(t.roles)).catch(() => {}), [pid])
  const refreshConversations = useCallback(() => api.conversations(pid).then(setConversations).catch(() => {}), [pid])
  const loadMessages = useCallback(
    (conv: string) =>
      api.messages(pid, conv, undefined, PAGE).then((ms) => {
        setMessages(ms)
        setHasMore(ms.length >= PAGE)
      }),
    [pid],
  )

  // Initial load per project.
  useEffect(() => {
    if (!pid) return
    setError(null)
    api.projects().then(setProjects).catch(() => {})
    api
      .project(pid)
      .then((p) => {
        setProject(p)
        setPaused(p.paused)
        setRateLimit(p.rate_limited && p.rate_limited.status !== 'allowed' ? p.rate_limited : null)
      })
      .catch((e) => setError(e.message))
    refreshConversations()
    refreshTeam()
    api.questions(pid, 'pending').then(setQuestions).catch(() => {})
  }, [pid, refreshConversations, refreshTeam])

  useEffect(() => {
    if (!pid) return
    const id = setInterval(refreshTeam, 30000)
    return () => clearInterval(id)
  }, [pid, refreshTeam])

  useEffect(() => {
    if (!pid) return
    setMessages([])
    loadMessages(current).catch((e) => setError(e.message))
  }, [pid, current, loadMessages])

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
    switch (f.event) {
      case 'message': {
        const m = f.data as Message
        if (m.conversation_id === current) setMessages((ms) => (ms.some((x) => x.id === m.id) ? ms : [...ms, m]))
        setConversations((cs) => cs.map((c) => (c.id === m.conversation_id ? { ...c, last_message_at: m.created_at } : c)))
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
        setTeam((ts) => ts.map((r) => (r.role === t.role ? { ...r, state: t.state === 'running' ? 'running' : t.state === 'queued' ? 'queued' : 'idle' } : r)))
        refreshTeam()
        break
      }
      case 'question': {
        const q = f.data as Question
        setQuestions((qs) => (qs.some((x) => x.id === q.id) ? qs.map((x) => (x.id === q.id ? q : x)) : [...qs, q]))
        break
      }
      case 'rate_limit': {
        const info = f.data?.info as RateLimitInfo | undefined
        setRateLimit(info && info.status !== 'allowed' ? info : null)
        break
      }
      case 'paused':
        setPaused(!!f.data?.paused)
        break
    }
  }
  const onReconnect = () => {
    loadMessages(current).catch(() => {})
    refreshTeam()
    refreshConversations()
    api.questions(pid, 'pending').then((qs) => setQuestions((old) => [...old.filter((q) => q.state !== 'pending'), ...qs])).catch(() => {})
  }
  useProjectWs(pid, onFrame, onReconnect)

  const conv = conversations.find((c) => c.id === current)
  const dmRole = current.startsWith('dm-') ? current.slice(3) : null
  const title = conv ? (conv.kind === 'channel' ? `# ${conv.title}` : conv.kind === 'dm' ? roleName(conv.role || dmRole || '') : conv.title) : dmRole ? roleName(dmRole) : current === 'general' ? '# general' : current
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
  }

  return (
    <div className="h-full flex">
      <Sidebar
        projects={projects}
        project={project}
        conversations={conversations}
        team={team}
        current={current}
        paused={paused}
        onPickProject={(id) => nav(`/p/${id}`)}
        onSelect={(id) => select(id)}
        onNewConversation={async (t) => {
          const c = await api.createConversation(pid, t)
          setConversations((cs) => [c, ...cs])
          select(c.id)
        }}
        onPause={(p) => {
          setPaused(p)
          api.pause(pid, p).then((r) => setPaused(r.paused)).catch(() => setPaused(!p))
        }}
      />
      <main className="flex-1 flex flex-col min-w-0 h-full">
        <Banners rateLimit={rateLimit} paused={paused} pending={pending} onAnswer={(q) => select(`dm-${q.role}`)} />
        <TeamStrip team={team} onSelect={(r) => select(`dm-${r}`)} />
        <div className="px-4 py-2 border-b border-gray-200 flex items-center gap-4 shrink-0">
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
          {error && <span className="ml-auto text-xs text-red-600 truncate">{error}</span>}
        </div>

        {dmRole && tab === 'live' ? (
          <LiveSession pid={pid} role={dmRole} subscribe={subscribe} />
        ) : (
          <>
            <MessageList
              messages={messages}
              hasMore={hasMore}
              onLoadMore={loadMore}
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
  )
}
