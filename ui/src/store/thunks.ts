import type { UnknownAction } from '@reduxjs/toolkit'
import type { Feature, InboxMessage, Question, Standup } from '../api'
import { alertFor, type Alert } from '../alerts'
import { api } from '../api'
import { toastFor, type Toast } from '../toasts'
import type { WsFrame } from '../ws'
import * as ev from './actions'
import type { AppThunk } from './index'
import { conversations, dashboard, features, inbox, locks, messages, questions, session, team } from './slices'

export const PAGE = 50
export const VIEWS = ['dashboard', 'inbox', 'history']

const errText = (e: any, fallback: string) => (typeof e?.message === 'string' && e.message) || fallback

// Fetch for the open project and apply the result only if the Master did not switch projects meanwhile.
const refresh =
  <T>(fetch: (pid: string) => Promise<T>, apply: (v: T) => UnknownAction): AppThunk<Promise<void>> =>
  async (dispatch, getState) => {
    const pid = getState().session.pid
    if (!pid) return
    try {
      const v = await fetch(pid)
      if (getState().session.pid === pid) dispatch(apply(v))
    } catch {
      /* the next WS frame or reconnect refetches */
    }
  }

export const refreshHealth = () => refresh(() => api.health(), session.actions.healthLoaded)
export const refreshTeam = () => refresh(api.team, (t) => team.actions.loaded(t.roles))
export const refreshDashboard = () => refresh(api.dashboard, dashboard.actions.loaded)
export const refreshConversations = () => refresh(api.conversations, conversations.actions.loaded)
export const refreshFeatures = () => refresh((pid) => api.features(pid, true), features.actions.loaded)
export const refreshLocks = () => refresh(api.locks, locks.actions.loaded)
export const refreshInbox = () => refresh(api.inbox, inbox.actions.loaded)

export const openProject =
  (pid: string): AppThunk =>
  (dispatch) => {
    dispatch(ev.projectOpened(pid))
    api.projects().then((ps) => dispatch(session.actions.projectsLoaded(ps))).catch(() => {})
    api
      .project(pid)
      .then((p) => dispatch(session.actions.projectLoaded(p)))
      .catch((e) => dispatch(session.actions.errorSet(errText(e, 'could not load the project'))))
    dispatch(refreshHealth())
    dispatch(refreshConversations())
    dispatch(refreshTeam())
    dispatch(refreshFeatures())
    dispatch(refreshLocks())
    dispatch(refreshInbox())
    dispatch(refreshDashboard())
    dispatch(refresh((p) => api.questions(p, 'pending'), questions.actions.loaded))
  }

// Opening a conversation: empty list at once, then its last page (views like the dashboard load nothing).
export const openConversation =
  (conversation: string): AppThunk =>
  (dispatch) => {
    dispatch(messages.actions.cleared(conversation))
    if (conversation === 'inbox') dispatch(refreshInbox())
    else if (conversation === 'dashboard') dispatch(refreshDashboard())
    if (VIEWS.includes(conversation)) return
    dispatch(loadMessages(conversation)).catch((e) => dispatch(session.actions.errorSet(errText(e, 'could not load messages'))))
  }

export const loadMessages =
  (conversation: string): AppThunk<Promise<void>> =>
  async (dispatch, getState) => {
    dispatch(messages.actions.loadStarted(conversation))
    let items
    try {
      items = await api.messages(getState().session.pid, conversation, undefined, PAGE)
    } catch (e) {
      dispatch(messages.actions.loadFailed(conversation))
      throw e
    }
    dispatch(messages.actions.loaded({ conversation, items, hasMore: items.length >= PAGE }))
  }

export const loadOlderMessages = (): AppThunk<Promise<void>> => async (dispatch, getState) => {
  const { session: s, messages: m } = getState()
  const first = m.items[0]
  if (!first || !m.conversation) return
  const items = await api.messages(s.pid, m.conversation, first.id, PAGE)
  dispatch(messages.actions.olderLoaded({ conversation: m.conversation, items, hasMore: items.length >= PAGE }))
}

// F-28: the alert a live frame calls for (sound and pop-up), or null. Judged on the store's features, like toasts.
export const alertReceived =
  (f: WsFrame): AppThunk<Alert | null> =>
  (_dispatch, getState) =>
    alertFor(f, { features: getState().features })

// One WS frame -> domain actions + follow-up fetches. Returns the toast it calls for (judged on the state before
// the frame is applied), or null. `current` is the open view: the dashboard refetches only while on screen.
export const frameReceived =
  (f: WsFrame, current: string): AppThunk<Toast | null> =>
  (dispatch, getState) => {
    const before = getState()
    const toast = toastFor(f, { features: before.features, prevRateLimit: before.session.rateLimit })
    // waits_on / stale / counts only come from /dashboard.
    const touchDash = () => {
      if (current === 'dashboard') dispatch(refreshDashboard())
    }
    switch (f.event) {
      case 'message':
        dispatch(ev.messageReceived(f.data))
        break
      case 'receipt':
        dispatch(ev.receiptReceived(f.data))
        break
      case 'reaction':
        dispatch(ev.reactionsReceived({ message_id: f.data?.message_id, reactions: Array.isArray(f.data?.reactions) ? f.data.reactions : [] }))
        break
      case 'turn_state':
        dispatch(ev.turnStateReceived(f.data))
        dispatch(refreshTeam())
        break
      case 'team': {
        const list = Array.isArray(f.data) ? f.data : f.data?.roles
        if (Array.isArray(list)) dispatch(ev.teamReceived(list))
        break
      }
      case 'activity':
        dispatch(ev.activityReceived(f.data))
        break
      case 'standup':
        dispatch(ev.standupUpdated(f.data))
        break
      case 'conversation':
        dispatch(ev.conversationUpserted(f.data))
        break
      case 'question':
        dispatch(ev.questionUpdated(f.data))
        touchDash()
        break
      case 'feature':
        dispatch(ev.featureUpserted(f.data))
        touchDash()
        break
      case 'approval':
        dispatch(ev.approvalUpdated(f.data))
        touchDash()
        break
      case 'commit':
        dispatch(ev.commitUpdated(f.data))
        touchDash()
        break
      case 'locks':
        dispatch(ev.locksReceived(Array.isArray(f.data?.locks) ? f.data.locks : []))
        break
      case 'inbox':
        // The count lands at once; the full inbox is refetched so the view and badge stay exact.
        if (typeof f.data?.count === 'number') dispatch(ev.inboxCountReceived(f.data.count))
        dispatch(refreshInbox())
        touchDash()
        break
      case 'rate_limit':
        dispatch(ev.rateLimitReceived(f.data?.info ?? null))
        break
      case 'paused':
        dispatch(ev.pausedReceived(!!f.data?.paused))
        break
    }
    return toast
  }

// After a WS reconnect: refetch everything a missed frame could have changed.
export const reconnected =
  (current: string): AppThunk =>
  (dispatch) => {
    if (!VIEWS.includes(current)) dispatch(loadMessages(current)).catch(() => {})
    dispatch(refreshHealth())
    dispatch(refreshTeam())
    dispatch(refreshConversations())
    dispatch(refreshFeatures())
    dispatch(refreshLocks())
    dispatch(refreshInbox())
    dispatch(refreshDashboard())
    dispatch(refresh((p) => api.questions(p, 'pending'), questions.actions.pendingMerged))
  }

// ---- Master actions ------------------------------------------------------------------------------------
export const answerQuestion =
  (q: Question, answers: Record<string, string>): AppThunk<Promise<void>> =>
  async (dispatch, getState) => {
    dispatch(ev.questionUpdated(await api.answerQuestion(getState().session.pid, q.id, answers)))
  }

export const resolveInboxMessage =
  (m: InboxMessage): AppThunk<Promise<void>> =>
  async (dispatch, getState) => {
    await api.resolveInbox(getState().session.pid, m.id)
    dispatch(inbox.actions.messageResolved(m.id))
  }

export const patchFeature =
  (id: number, patch: { phase?: string; owner?: string | null; lead?: string; archived?: boolean }): AppThunk<Promise<void>> =>
  async (dispatch, getState) => {
    try {
      dispatch(ev.featureUpserted(await api.patchFeature(getState().session.pid, id, patch)))
    } catch (e) {
      dispatch(session.actions.errorSet(errText(e, 'patch failed')))
      throw e
    }
  }

export const freshStartFeature =
  (f: Feature, mode: 'keep_docs' | 'start_over'): AppThunk<Promise<void>> =>
  async (dispatch, getState) => {
    try {
      await api.freshStartFeature(getState().session.pid, f.id, mode)
      dispatch(refreshConversations())
      dispatch(refreshFeatures())
      await dispatch(loadMessages(f.conversation_id))
    } catch (e) {
      dispatch(session.actions.errorSet(errText(e, 'fresh start failed')))
      throw e
    }
  }

export const stopRole =
  (role: string): AppThunk<Promise<void>> =>
  async (dispatch, getState) => {
    await api.stopRole(getState().session.pid, role)
    dispatch(refreshTeam())
  }

export const freshStartRole =
  (role: string, handoff: boolean): AppThunk<Promise<void>> =>
  async (dispatch, getState) => {
    await api.freshStartRole(getState().session.pid, role, handoff)
    dispatch(refreshTeam())
  }

export const startStandup = (): AppThunk<Promise<Standup | { status: string }>> => async (dispatch, getState) => {
  const r = await api.startStandup(getState().session.pid)
  if (!('status' in r)) dispatch(dashboard.actions.standupStarted(r))
  return r
}

export const releaseLock =
  (path: string): AppThunk<Promise<void>> =>
  async (dispatch, getState) => {
    try {
      await api.releaseLock(getState().session.pid, path)
      dispatch(locks.actions.released(path))
    } catch (e) {
      dispatch(session.actions.errorSet(errText(e, 'release failed')))
    }
  }

export const createTask =
  (body: { text: string; starter: string }): AppThunk<Promise<Feature>> =>
  async (dispatch, getState) => {
    const f = await api.createFeature(getState().session.pid, body)
    dispatch(ev.featureUpserted(f))
    return f
  }

// Optimistic toggle; the response (or a failure) settles it.
export const setPaused =
  (paused: boolean): AppThunk =>
  (dispatch, getState) => {
    dispatch(ev.pausedReceived(paused))
    api
      .pause(getState().session.pid, paused)
      .then((r) => dispatch(ev.pausedReceived(r.paused)))
      .catch(() => dispatch(ev.pausedReceived(!paused)))
  }
