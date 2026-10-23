// F-28: sound and desktop alerts for the Master. `alertFor` is the pure part (which live frame is an alert, in which
// group); the rest is the per-browser settings, the "only one tab alerts" lock and the pop-up. Like toasts, alerts are
// built only from live WS frames, so nothing alerts on load or reconnect.
import { useSyncExternalStore } from 'react'
import type { Approval, Commit, Feature, Message, Question } from './api'
import { ensureAudio, playTone } from './alertSound'
import { roleName } from './roles'
import { convFor } from './toasts'
import type { WsFrame } from './ws'

// sign-off: approvals and commit requests; answer: cards, Questions and Blockers; status: other messages to @master.
export type AlertGroup = 'sign-off' | 'answer' | 'status'

export type Alert = {
  key: string
  group: AlertGroup
  title: string
  body: string
  conversation: string
  message?: number
}

const BODY_MAX = 120

// First line, leading @mentions dropped, cut to a notification-sized line.
const oneLine = (s: string | null | undefined) => {
  const line = (s || '').trim().replace(/^(@[\w-]+[\s,]*)+/, '').split('\n')[0].trim()
  return line.length > BODY_MAX ? `${line.slice(0, BODY_MAX - 1).trimEnd()}…` : line
}

const MESSAGE_LABEL: Record<string, string> = { Message: 'Message for you' }

export function alertFor(f: WsFrame, ctx: { features: Feature[] }): Alert | null {
  const keyOf = (featureId: number | null | undefined) => ctx.features.find((x) => x.id === featureId)?.key
  const named = (label: string, featureKey: string | undefined) => (featureKey ? `${label} · ${featureKey}` : label)
  switch (f.event) {
    case 'approval': {
      const a = f.data as Approval
      if (a?.state !== 'pending') return null
      return {
        key: `approval:${a.id}`,
        group: 'sign-off',
        title: named('Approval needed', keyOf(a.feature_id)),
        body: `${roleName(a.role)}: ${oneLine(a.title)}`,
        conversation: convFor(a.feature_id, a.role, ctx.features),
        message: a.message_id ?? undefined,
      }
    }
    case 'commit': {
      const c = f.data as Commit
      if (c?.state !== 'master_review') return null
      return {
        key: `commit:${c.id}:master_review`,
        group: 'sign-off',
        title: named('Commit request', keyOf(c.feature_id)),
        body: `${roleName(c.role)}: ${oneLine(c.message)}`,
        conversation: convFor(c.feature_id, c.role, ctx.features),
        message: c.message_id ?? undefined,
      }
    }
    case 'question': {
      const q = f.data as Question
      if (q?.state !== 'pending') return null
      // A card opens its own conversation at its message; a blocking question shows in the role's DM.
      const card = q.kind === 'card' && q.conversation_id
      const feature = card ? ctx.features.find((x) => x.conversation_id === q.conversation_id) : undefined
      return {
        key: `question:${q.id}`,
        group: 'answer',
        title: named('Question', feature?.key),
        body: `${roleName(q.role)}: ${oneLine(q.payload?.questions?.[0]?.question)}`,
        conversation: card ? q.conversation_id! : `dm-${q.role}`,
        message: card ? (q.message_id ?? undefined) : undefined,
      }
    }
    case 'message': {
      const m = f.data as Message
      if (!m || !m.mentions?.includes('master') || m.author === 'master') return null
      // A message with a `ref` is the chat copy of an approval, commit request or card, which already alerted by its own
      // frame (or a round's System line): skipping it gives one alert per item.
      if (m.ref) return null
      const group: AlertGroup = m.type === 'Question' || m.type === 'Blocker' ? 'answer' : 'status'
      const feature = ctx.features.find((x) => x.conversation_id === m.conversation_id)
      return {
        key: `msg:${m.id}`,
        group,
        title: named(MESSAGE_LABEL[m.type] || m.type, feature?.key),
        body: `${roleName(m.author)}: ${oneLine(m.text)}`,
        conversation: m.conversation_id,
        message: m.id,
      }
    }
  }
  return null
}

// ---- Settings: per browser (this is the Master's device preference), both on by default --------------------------
const SETTING_KEYS = { sound: 'aiteams.alerts.sound', popup: 'aiteams.alerts.popup' } as const
export type AlertSetting = keyof typeof SETTING_KEYS

const settingListeners = new Set<() => void>()
const emitSettings = () => settingListeners.forEach((f) => f())

export const alertSetting = (s: AlertSetting) => {
  try {
    return !['false', 'off', '0'].includes((localStorage.getItem(SETTING_KEYS[s]) || '').toLowerCase())
  } catch {
    return true // storage unavailable: the default
  }
}

export function setAlertSetting(s: AlertSetting, on: boolean) {
  try {
    localStorage.setItem(SETTING_KEYS[s], String(on))
  } catch {
    /* storage unavailable: lasts until reload */
  }
  emitSettings()
  // The click that turned a switch on is the gesture the browser wants for sound and for the permission prompt.
  if (on && s === 'sound') ensureAudio(true)
  if (on && s === 'popup') requestPopupPermission()
}

const subscribeSettings = (cb: () => void) => {
  settingListeners.add(cb)
  window.addEventListener('storage', cb) // another tab changed it
  return () => {
    settingListeners.delete(cb)
    window.removeEventListener('storage', cb)
  }
}

export const useAlertSetting = (s: AlertSetting) => useSyncExternalStore(subscribeSettings, () => alertSetting(s))

// ---- One tab alerts -------------------------------------------------------------------------------------------------
// The tab holding the Web Lock alerts; the lock is held until the tab closes, then passes to a waiting tab. The name
// carries the project, because a tab only sees its own project's frames (one lock for all would silence a second project).
// Without `navigator.locks` every tab alerts.
let alerting = typeof navigator === 'undefined' || !('locks' in navigator)
const lockListeners = new Set<() => void>()

export const isAlerting = () => alerting
const setAlerting = (v: boolean) => {
  alerting = v
  lockListeners.forEach((f) => f())
}
export const useIsAlerting = () =>
  useSyncExternalStore(
    (cb) => {
      lockListeners.add(cb)
      return () => lockListeners.delete(cb)
    },
    () => alerting,
  )

// Returns the release function (the effect cleanup): a project switch or unmount hands the lock on.
export function claimAlertLock(pid: string): () => void {
  if (!pid || typeof navigator === 'undefined' || !('locks' in navigator)) return () => {}
  const ctl = new AbortController()
  let release = () => {}
  setAlerting(false)
  navigator.locks
    .request(`aiteams-alerts:${pid}`, { signal: ctl.signal }, () => {
      setAlerting(true)
      return new Promise<void>((resolve) => (release = resolve)) // never resolves until we let go
    })
    .catch(() => {}) // aborted while still waiting
  return () => {
    ctl.abort()
    release()
    setAlerting(false)
  }
}

// ---- Pop-up -----------------------------------------------------------------------------------------------------------
const permListeners = new Set<() => void>()
const emitPermission = () => permListeners.forEach((f) => f())
export type PopupPermission = NotificationPermission | 'unsupported'
export const popupPermission = (): PopupPermission => (typeof Notification === 'undefined' ? 'unsupported' : Notification.permission)

export const usePopupPermission = () =>
  useSyncExternalStore(
    (cb) => {
      permListeners.add(cb)
      // The Master may change it in the browser's site settings and come back.
      window.addEventListener('focus', cb)
      return () => {
        permListeners.delete(cb)
        window.removeEventListener('focus', cb)
      }
    },
    popupPermission,
  )

export function requestPopupPermission() {
  if (popupPermission() !== 'default') return
  try {
    void Promise.resolve(Notification.requestPermission()).finally(emitPermission)
  } catch {
    /* an old Safari without the promise form */
  }
}

function showPopup(a: Alert, onOpen: (a: Alert) => void) {
  if (popupPermission() !== 'granted') return
  try {
    const n = new Notification(a.title, { body: a.body, tag: a.key })
    n.onclick = () => {
      window.focus()
      n.close()
      onOpen(a)
    }
  } catch {
    /* some mobile browsers only allow notifications from a service worker */
  }
}

// Play the group's tone and show the pop-up, each only when its switch is on. Only the tab holding the lock does either.
export function deliverAlert(a: Alert, onOpen: (a: Alert) => void) {
  if (!alerting) return
  if (alertSetting('sound')) playTone(a.group)
  if (alertSetting('popup')) showPopup(a, onOpen)
}

// The first click or key press is the gesture browsers require: resume the audio context and ask for permission then.
// Returns the cleanup.
export function armAlertGestures(): () => void {
  const off = () => {
    window.removeEventListener('pointerdown', onGesture, true)
    window.removeEventListener('keydown', onGesture, true)
  }
  const onGesture = () => {
    off()
    if (alertSetting('sound')) ensureAudio(true)
    if (alertSetting('popup')) requestPopupPermission()
  }
  if (alertSetting('sound')) ensureAudio(false) // so "still suspended" is known before the first click
  window.addEventListener('pointerdown', onGesture, true)
  window.addEventListener('keydown', onGesture, true)
  return off
}
