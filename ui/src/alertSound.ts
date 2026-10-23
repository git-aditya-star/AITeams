// F-28: the three alert tones, synthesized with the Web Audio API (no audio files). Each alert is ONE oscillator whose
// pitch steps through the tone's notes under a gain envelope, so one tone = one oscillator = one recordable event.
import { useSyncExternalStore } from 'react'
import type { AlertGroup } from './alerts'

type Note = { f: number; at: number; len: number } // frequency (Hz), start offset and length (s)

const TONES: Record<AlertGroup, Note[]> = {
  'sign-off': [
    { f: 523.25, at: 0, len: 0.16 }, // C5, then up to G5: needs your sign-off
    { f: 783.99, at: 0.16, len: 0.26 },
  ],
  answer: [
    { f: 880, at: 0, len: 0.11 }, // three quick A5 / A5 / C6 notes: needs an answer
    { f: 880, at: 0.16, len: 0.11 },
    { f: 1046.5, at: 0.32, len: 0.2 },
  ],
  status: [{ f: 329.63, at: 0, len: 0.4 }], // one soft low E4: for your information
}

// The first frequency of each tone. Tests (and QA's walk) map a recorded sound back to its group with it.
export const TONE_FREQ: Record<AlertGroup, number> = {
  'sign-off': TONES['sign-off'][0].f,
  answer: TONES.answer[0].f,
  status: TONES.status[0].f,
}

const PEAK = 0.22
const FLOOR = 0.0001

let ctx: AudioContext | null = null
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((f) => f())

// The shared context, made on first use. `resume` is for a user gesture: browsers keep a new context suspended until one.
export function ensureAudio(resume: boolean): AudioContext | null {
  if (!ctx) {
    const Ctor: typeof AudioContext | undefined = typeof window === 'undefined' ? undefined : (window.AudioContext ?? (window as any).webkitAudioContext)
    if (!Ctor) return null
    try {
      ctx = new Ctor()
    } catch {
      return null
    }
    ctx.addEventListener('statechange', emit)
    emit()
  }
  if (resume && ctx.state !== 'running') void ctx.resume().catch(() => {})
  return ctx
}

export function playTone(group: AlertGroup) {
  const c = ensureAudio(false)
  // A suspended context would queue the tone and sound it, late, at the next click.
  if (!c || c.state !== 'running') return
  const notes = TONES[group]
  const t0 = c.currentTime + 0.02
  const osc = c.createOscillator()
  const gain = c.createGain()
  osc.type = 'sine'
  osc.frequency.value = notes[0].f
  gain.gain.value = 0
  for (const n of notes) {
    const t = t0 + n.at
    osc.frequency.setValueAtTime(n.f, t)
    gain.gain.setValueAtTime(FLOOR, t)
    gain.gain.exponentialRampToValueAtTime(PEAK, t + 0.02)
    gain.gain.exponentialRampToValueAtTime(FLOOR, t + n.len)
  }
  const end = t0 + notes[notes.length - 1].at + notes[notes.length - 1].len
  osc.connect(gain)
  gain.connect(c.destination)
  osc.start(t0)
  osc.stop(end + 0.05)
}

// 'none' until a context exists (sound off, or no Web Audio), then the context's own state.
export type AudioStatus = AudioContextState | 'none'
export const audioStatus = (): AudioStatus => ctx?.state ?? 'none'
export const useAudioStatus = () =>
  useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    audioStatus,
  )
