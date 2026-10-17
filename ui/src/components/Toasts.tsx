import { useEffect, useRef, useState } from 'react'
import type { Toast } from '../toasts'

const MAX = 4

function ToastItem({ t, onOpen, onClose }: { t: Toast; onOpen: () => void; onClose: () => void }) {
  const [hover, setHover] = useState(false)
  // onClose is a fresh closure each render; keep it out of the effect deps so re-renders don't reset the timer.
  const close = useRef(onClose)
  close.current = onClose
  // Timer runs only while not hovered; leaving restarts it at the full duration.
  useEffect(() => {
    if (hover) return
    const id = setTimeout(() => close.current(), t.tone === 'error' ? 10000 : 6000)
    return () => clearTimeout(id)
  }, [hover, t.tone])
  return (
    <div
      role="status"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className={`w-96 bg-white border border-gray-200 border-l-4 ${t.tone === 'error' ? 'border-l-red-500' : 'border-l-blue-500'} rounded shadow-sm text-[13px] text-gray-800 flex items-start`}
    >
      {/* Up to 5 lines; the full text is in the hover title (hover also pauses the timer). */}
      <button className="flex-1 min-w-0 text-left px-3 py-2 break-words line-clamp-5" title={t.text} onClick={onOpen}>
        {t.text}
      </button>
      <button className="shrink-0 px-2 py-1.5 text-gray-400 hover:text-gray-700" aria-label="Dismiss" onClick={onClose}>
        ×
      </button>
    </div>
  )
}

// Add a toast, dropping the oldest beyond MAX (dropped toasts are gone, not queued).
export const pushToast = (ts: Toast[], t: Toast) => [...ts, t].slice(-MAX)

// Bottom-right stack, newest on top; `toasts` is oldest-first.
export default function Toasts({ toasts, onOpen, onDismiss }: { toasts: Toast[]; onOpen: (t: Toast) => void; onDismiss: (key: string) => void }) {
  if (!toasts.length) return null
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {[...toasts]
        .reverse()
        .map((t) => (
          <ToastItem key={t.key} t={t} onOpen={() => onOpen(t)} onClose={() => onDismiss(t.key)} />
        ))}
    </div>
  )
}
