import { Bell, Check, Clock, MessageCircleQuestion, Puzzle, RotateCw, Square, TriangleAlert, X, type LucideIcon } from 'lucide-react'
import { toast as sonner } from 'sonner'
import { KINDS, type Kind, type Tone, type Toast } from '../toasts'
import { Button } from './ui/button'
import { Toaster } from './ui/sonner'
import { Tip } from './ui/tooltip'

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

const ICON: Record<Kind, LucideIcon> = {
  question: MessageCircleQuestion,
  blocker: TriangleAlert,
  approval: Bell,
  commit_request: Bell,
  committed: Check,
  commit_failed: TriangleAlert,
  standup: Bell,
  stopped: Square,
  interrupted: Square,
  fresh_session: RotateCw,
  rate_limit: Clock,
  plugin_missing: Puzzle,
}

// The F-1 rev3 card, rendered by Sonner through toast.custom (Sonner owns stacking and the 15 s timer).
function ToastCard({ t, onOpen, onClose }: { t: Toast; onOpen: () => void; onClose: () => void }) {
  const tone = TONE[t.tone]
  const Icon = ICON[t.kind]
  const pulse = t.tone === 'action' || t.tone === 'error'
  return (
    <div role="status" data-toast={t.kind} className="relative w-96 bg-white rounded-xl shadow-xl ring-1 ring-black/5 overflow-hidden flex items-start gap-3 pl-3 pr-2 py-3">
      <span className={`absolute inset-y-0 left-0 w-1 ${tone.accent}`} />
      <span className={`ml-1 shrink-0 w-9 h-9 rounded-full flex items-center justify-center ${tone.circle} ${tone.text} ${pulse ? 'motion-safe:animate-ring-pulse' : ''}`}>
        <Icon size={18} />
      </span>
      {/* Up to 5 lines; the full text is in the hover title. */}
      <button className="flex-1 min-w-0 cursor-pointer text-left wrap-break-word" title={t.text} onClick={onOpen}>
        <span className={`block text-caption font-semibold uppercase tracking-wide ${tone.text}`}>{KINDS[t.kind].label}</span>
        <span className="text-body text-gray-800 line-clamp-5">{t.text}</span>
      </button>
      <Tip content="Dismiss">
        <Button variant="ghost" size="icon-xs" className="text-gray-400" aria-label="Dismiss" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </Tip>
      {/* Paused with Sonner's timer while the pointer is over the stack (index.css). */}
      <span className={`toast-bar absolute bottom-0 left-0 h-[3px] w-full origin-left ${tone.accent} motion-safe:animate-toast-bar`} />
    </div>
  )
}

// Show one live-event toast; `t.key` is the Sonner id, so a repeated key replaces instead of stacking.
export function showToast(t: Toast, onOpen: (t: Toast) => void) {
  sonner.custom(
    (id) => (
      <ToastCard
        t={t}
        onOpen={() => {
          sonner.dismiss(id)
          onOpen(t)
        }}
        onClose={() => sonner.dismiss(id)}
      />
    ),
    { id: t.key, duration: DISMISS_MS },
  )
}

// Bottom-right column, newest at the bottom edge, up to MAX visible.
export default function Toasts() {
  return <Toaster position="bottom-right" expand visibleToasts={MAX} gap={12} offset={20} style={{ '--width': '384px' } as React.CSSProperties} />
}
