import type { BrowserContext, Page } from '@playwright/test'

// F-28: stubs for the two browser APIs behind alerts, so a spec (and QA's walk) can see what the app would have played
// and shown. They live in test code only: the app never reads `__alertStub` or writes `__alerts`.
//
//   window.__alertStub = {permission: 'granted' | 'denied' | 'default', audio: 'running' | 'suspended'}   (set before load;
//       defaults granted / running; optional `permissionAnswer: 'granted' | 'denied'` is what requestPermission() answers)
//   window.__alerts = [{via: 'sound', freq}, {via: 'popup', key, title}, ...]   in the order they happened:
//       a sound is recorded when its oscillator starts (freq = the tone's first frequency, see TONE_FREQ in
//       src/alertSound.ts), a pop-up when `new Notification(title, {tag})` runs (key = tag)
//   window.__notifications = the Notification instances, so a spec can call `.onclick()` as a click on the pop-up
//   window.__notificationRequests = how many times Notification.requestPermission() was called
export type AlertStubConfig = { permission?: 'granted' | 'denied' | 'default'; audio?: 'running' | 'suspended'; permissionAnswer?: 'granted' | 'denied' }
export type RecordedAlert = { via: 'sound'; freq: number } | { via: 'popup'; key: string; title: string }

// Runs in the page, before its own scripts: it must not touch anything outside itself.
export function alertStubScript() {
  const w = window as any
  const cfg = { permission: 'granted', audio: 'running', permissionAnswer: 'granted', ...(w.__alertStub || {}) }
  w.__alerts = []
  w.__notifications = []
  w.__notificationRequests = 0

  class StubAudioContext extends EventTarget {
    state = cfg.audio
    currentTime = 0
    destination = {}
    createOscillator() {
      const osc: any = {
        type: 'sine',
        frequency: { value: 440, setValueAtTime() {} },
        connect() {},
        stop() {},
        start() {
          w.__alerts.push({ via: 'sound', freq: osc.frequency.value })
        },
      }
      return osc
    }
    createGain() {
      return { gain: { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }
    }
    // A real context resumes when asked from a user gesture; the app only asks from one.
    resume() {
      if (this.state !== 'running') {
        this.state = 'running'
        this.dispatchEvent(new Event('statechange'))
      }
      return Promise.resolve()
    }
  }
  w.AudioContext = StubAudioContext

  class StubNotification {
    static permission = cfg.permission
    static requestPermission() {
      w.__notificationRequests += 1
      StubNotification.permission = cfg.permissionAnswer
      return Promise.resolve(StubNotification.permission)
    }
    onclick: null | (() => void) = null
    constructor(
      public title: string,
      public options: { tag?: string; body?: string } = {},
    ) {
      w.__alerts.push({ via: 'popup', key: options.tag, title })
      w.__notifications.push(this)
    }
    close() {}
  }
  w.Notification = StubNotification
}

// Page or context: a context also covers the pages opened later (the several-tabs case).
export async function installAlertStubs(target: Page | BrowserContext, cfg: AlertStubConfig = {}) {
  await target.addInitScript((c) => {
    ;(window as any).__alertStub = c
  }, cfg)
  await target.addInitScript(alertStubScript)
}

export const recorded = (page: Page) => page.evaluate(() => (window as any).__alerts as RecordedAlert[])
