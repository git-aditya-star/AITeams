import { useEffect, useRef, useState } from 'react'
import { KINDS, type Kind, type Tone, type Toast } from '../toasts'
import { AlertTriangle, Bell, Check, Clock, MessageQuestion, Refresh, Stop } from './icons'

const MAX = 4
const DISMISS_MS = 15000

// Full class strings per tone, so Tailwind keeps them.
const TONE: Record<Tone, { accent: string; text: string; circle: string }> = {
  action: { accent: 'bg-indigo-600', text: 'text-indigo-600', circle: 'bg-indigo-50' },
  success: { accent: 'bg-emerald-600', text: 'text-emerald-600', circle: 'bg-emerald-50' },
  warning: { accent: 'bg-amber-500', text: 'text-amber-500', circle: 'bg-amber-50' },
  error: { accent: 'bg-rose-600', text: 'text-rose-600', circle: 'bg-rose-50' },
  info: { accent: 'bg-sky-600', text: 'text-sky-600', circle: 'bg-sky-50' },
}

const ICON: Record<Kind, (p: { size?: number }) => JSX.Element> = {
  question: MessageQuestion,
  blocker: AlertTriangle,
  approval: Bell,
  commit_request: Bell,
  committed: Check,
  commit_failed: AlertTriangle,
  standup: Bell,
  stopped: Stop,
  interrupted: Stop,
  fresh_session: Refresh,
  rate_limit: Clock,
}

function ToastItem({ t, onOpen, onClose }: { t: Toast; onOpen: () => void; onClose: () => void }) {
  const [hover, setHover] = useState(false)
  // Bumped on mouse-leave so the countdown bar restarts from full width along with the timer.
  const [round, setRound] = useState(0)
  // onClose is a fresh closure each render; keep it out of the effect deps so re-renders don't reset the timer.
  const close = useRef(onClose)
  close.current = onClose
  // Timer runs only while not hovered; leaving restarts it at the full duration.
  useEffect(() => {
    if (hover) return
    const id = setTimeout(() => close.current(), DISMISS_MS)
    return () => clearTimeout(id)
  }, [hover])

  const tone = TONE[t.tone]
  const Icon = ICON[t.kind]
  const pulse = t.tone === 'action' || t.tone === 'error'
  return (
    <div
      role="status"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => {
        setHover(false)
        setRound((r) => r + 1)
      }}
      className="relative w-96 bg-white rounded-xl shadow-xl ring-1 ring-black/5 overflow-hidden flex items-start gap-3 pl-3 pr-2 py-3 motion-safe:animate-toast-in"
    >
      <span className={`absolute inset-y-0 left-0 w-1 ${tone.accent}`} />
      <span className={`ml-1 shrink-0 w-9 h-9 rounded-full flex items-center justify-center ${tone.circle} ${tone.text} ${pulse ? 'motion-safe:animate-ring-pulse' : ''}`}>
        <Icon size={18} />
      </span>
      {/* Up to 5 lines; the full text is in the hover title (hover also pauses the timer). */}
      <button className="flex-1 min-w-0 text-left break-words" title={t.text} onClick={onOpen}>
        <span className={`block text-[11px] font-semibold uppercase tracking-wide ${tone.text}`}>{KINDS[t.kind].label}</span>
        <span className="block text-[13.5px] text-gray-800 leading-snug line-clamp-5">{t.text}</span>
      </button>
      <button className="shrink-0 px-1.5 text-lg leading-none text-gray-400 hover:text-gray-700" aria-label="Dismiss" onClick={onClose}>
        ×
      </button>
      <span
        key={round}
        className={`absolute bottom-0 left-0 h-[3px] w-full origin-left ${tone.accent} motion-safe:animate-toast-bar`}
        style={{ animationPlayState: hover ? 'paused' : 'running' }}
      />
    </div>
  )
}

// Add a toast, dropping the oldest beyond MAX (dropped toasts are gone, not queued).
export const pushToast = (ts: Toast[], t: Toast) => [...ts, t].slice(-MAX)

// Bottom-right stack, newest on top; `toasts` is oldest-first.
export default function Toasts({ toasts, onOpen, onDismiss }: { toasts: Toast[]; onOpen: (t: Toast) => void; onDismiss: (key: string) => void }) {
  if (!toasts.length) return null
  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-3">
      {[...toasts]
        .reverse()
        .map((t) => (
          <ToastItem key={t.key} t={t} onOpen={() => onOpen(t)} onClose={() => onDismiss(t.key)} />
        ))}
    </div>
  )
}
