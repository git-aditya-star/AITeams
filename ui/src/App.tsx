import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { api } from './api'
import { armAlertGestures, claimAlertLock, deliverAlert } from './alerts'
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
import DashboardActions from './components/dashboard/DashboardActions'
import HistoryView from './components/HistoryView'
import RoleActions from './components/RoleActions'
import StandupButton from './components/StandupButton'
import { CardContext } from './components/CardContext'
import Toasts, { showToast } from './components/Toasts'
import CommitReview from './components/CommitReview'
import { Tabs, TabsList, TabsTrigger } from './components/ui/tabs'
import { useAppDispatch, useAppSelector } from './store'
import { selectDmQuestions, selectFeatureByConversation } from './store/selectors'
import { alertReceived, createTask, frameReceived, loadOlderMessages, openConversation, openProject, reconnected, refreshTeam } from './store/thunks'

type Tab = 'chat' | 'live' | 'docs'

// URL state (conversation, tab, highlighted message, commit review) lives in the query string; the open project's
// data lives in the Redux store (ui/src/store).
export default function App() {
  const { pid = '' } = useParams()
  const nav = useNavigate()
  const dispatch = useAppDispatch()
  const [params, setParams] = useSearchParams()
  const current = params.get('c') || 'dashboard'
  const tabParam = params.get('tab')
  const tab: Tab = tabParam === 'live' ? 'live' : tabParam === 'docs' ? 'docs' : 'chat'
  const highlight = Number(params.get('m')) || null
  const review = Number(params.get('review')) || null

  const conversations = useAppSelector((s) => s.conversations)
  const { items: messages, hasMore } = useAppSelector((s) => s.messages)
  const error = useAppSelector((s) => s.session.error)
  const feature = useAppSelector((s) => selectFeatureByConversation(s, current))
  const dmRole = current.startsWith('dm-') ? current.slice(3) : null
  const dmQuestions = useAppSelector((s) => selectDmQuestions(s, dmRole))
  // Toast keys already shown this page load (rate_limit is cleared when the limit clears).
  const seenToasts = useRef(new Set<string>())
  // F-28: alert keys already handled this page load, whether or not this tab was the one that played them.
  const seenAlerts = useRef(new Set<string>())

  // Fan-out for components (LiveSession) that want raw WS frames.
  const listeners = useRef(new Set<(f: WsFrame) => void>())
  const subscribe = useCallback((fn: (f: WsFrame) => void) => {
    listeners.current.add(fn)
    return () => {
      listeners.current.delete(fn)
    }
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
  // Commit review overlays the current view; closing drops only `review`, so the Master lands where they were.
  const openReview = useCallback(
    (id: number) =>
      setParams((p) => {
        const n = new URLSearchParams(p)
        n.set('review', String(id))
        return n
      }),
    [setParams],
  )
  const closeReview = useCallback(
    () =>
      setParams((p) => {
        const n = new URLSearchParams(p)
        n.delete('review')
        return n
      }),
    [setParams],
  )

  useEffect(() => {
    if (pid) dispatch(openProject(pid))
  }, [pid, dispatch])

  useEffect(() => {
    if (!pid) return
    const id = setInterval(() => dispatch(refreshTeam()), 30000)
    return () => clearInterval(id)
  }, [pid, dispatch])

  useEffect(() => {
    if (pid) dispatch(openConversation(current))
  }, [pid, current, dispatch])

  // F-28: one tab per project alerts (Web Lock), and the first click unlocks sound and the pop-up permission.
  useEffect(() => claimAlertLock(pid), [pid])
  useEffect(() => armAlertGestures(), [])

  const onFrame = (f: WsFrame) => {
    listeners.current.forEach((fn) => fn(f))
    const toast = dispatch(frameReceived(f, current))
    if (toast && !seenToasts.current.has(toast.key)) {
      seenToasts.current.add(toast.key)
      showToast(toast, (t) => {
        if (t.conversation) select(t.conversation, undefined, t.message)
      })
    }
    const alert = dispatch(alertReceived(f))
    if (alert && !seenAlerts.current.has(alert.key)) {
      seenAlerts.current.add(alert.key)
      deliverAlert(alert, (a) => select(a.conversation, undefined, a.message))
    }
    if (f.event === 'rate_limit' && (!f.data?.info || f.data.info.status.startsWith('allowed'))) seenToasts.current.delete('rate_limit')
  }
  useProjectWs(pid, onFrame, () => dispatch(reconnected(current)))

  const conv = conversations.find((c) => c.id === current)
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

  const newTask = async (body: { text: string; starter: string }) => {
    const f = await dispatch(createTask(body))
    select(f.conversation_id)
  }

  const cardActions = useMemo(() => ({ onReview: openReview }), [openReview])

  return (
    <CardContext.Provider value={cardActions}>
      <div className="h-full flex">
        <Sidebar current={current} onPickProject={(id) => nav(`/p/${id}`)} onSelect={(id) => select(id)} onNewTask={newTask} />
        <main className="relative flex-1 flex flex-col min-w-0 h-full">
          <Banners onAnswer={(q) => select(`dm-${q.role}`)} />
          <TeamStrip onSelect={(r) => select(`dm-${r}`)} />
          {feature ? (
            <FeatureHeader feature={feature} tab={tab === 'docs' ? 'docs' : 'chat'} onTab={(t) => select(current, t)} />
          ) : (
            <div className="px-4 py-2 border-b border-gray-200 flex items-center gap-4 shrink-0 flex-wrap">
              <h1 className="text-title truncate">{title}</h1>
              {dmRole && (
                <Tabs value={tab === 'live' ? 'live' : 'chat'} onValueChange={(t) => select(current, t as Tab)}>
                  <TabsList>
                    <TabsTrigger value="chat">Chat</TabsTrigger>
                    <TabsTrigger value="live">Live session</TabsTrigger>
                  </TabsList>
                </Tabs>
              )}
              {dmRole && <RoleActions role={dmRole} />}
              {current === 'standup' && <StandupButton />}
              {current === 'dashboard' && <DashboardActions onSelect={(id, m) => select(id, undefined, m)} onNewTask={newTask} />}
              {error && <span className="ml-auto text-caption text-red-600 truncate">{error}</span>}
            </div>
          )}

          {current === 'dashboard' ? (
            <DashboardView onSelect={(id, m) => select(id, undefined, m)} />
          ) : current === 'inbox' ? (
            <InboxView onOpen={(id) => select(id)} />
          ) : current === 'history' ? (
            <HistoryView onOpen={(id) => select(id)} />
          ) : feature && tab === 'docs' ? (
            <FeatureDocs pid={pid} featureId={feature.id} refreshKey={messages.length} />
          ) : dmRole && tab === 'live' ? (
            <LiveSession pid={pid} role={dmRole} subscribe={subscribe} />
          ) : (
            <>
              <MessageList
                messages={messages}
                hasMore={hasMore}
                onLoadMore={() => void dispatch(loadOlderMessages())}
                resetAfter={conv?.reset_after || 0}
                highlight={highlight}
                footer={dmQuestions.length ? dmQuestions.map((q) => <QuestionCard key={`${q.id}-${q.state}`} q={q} />) : undefined}
              />
              <ChatBox
                placeholder={dmRole ? `Message ${roleName(dmRole)}` : `Message ${title}`}
                onSend={async (text) => {
                  await api.sendMessage(pid, current, text)
                }}
              />
            </>
          )}
          {/* Overlay rather than a swap, so the view underneath keeps its scroll position. */}
          {review && (
            <div className="absolute inset-0 z-30 flex flex-col bg-gray-50">
              <CommitReview id={review} onClose={closeReview} />
            </div>
          )}
        </main>
      </div>
      <Toasts />
    </CardContext.Provider>
  )
}
