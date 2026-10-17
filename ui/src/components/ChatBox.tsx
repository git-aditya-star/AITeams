import { useRef, useState } from 'react'
import { ROLES, roleName } from '../roles'

export default function ChatBox({ onSend, placeholder }: { onSend: (text: string) => Promise<void>; placeholder: string }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [mention, setMention] = useState<{ start: number; prefix: string } | null>(null)
  const [sel, setSel] = useState(0)
  const ta = useRef<HTMLTextAreaElement>(null)

  const candidates = mention ? ROLES.filter((r) => r.startsWith(mention.prefix.toLowerCase())) : []

  const detectMention = (value: string, caret: number) => {
    const m = /(^|\s)@(\w*)$/.exec(value.slice(0, caret))
    if (m) {
      setMention({ start: caret - m[2].length - 1, prefix: m[2] })
      setSel(0)
    } else setMention(null)
  }

  const pick = (role: string) => {
    if (!mention) return
    const caret = ta.current?.selectionStart ?? text.length
    const next = `${text.slice(0, mention.start)}@${role} ${text.slice(caret)}`
    setText(next)
    setMention(null)
    requestAnimationFrame(() => {
      const pos = mention.start + role.length + 2
      ta.current?.setSelectionRange(pos, pos)
      ta.current?.focus()
    })
  }

  const send = async () => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true)
    setErr(null)
    try {
      await onSend(t)
      setText('')
      setMention(null)
    } catch (e: any) {
      setErr(e?.message || 'send failed')
    } finally {
      setBusy(false)
      ta.current?.focus()
    }
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mention && candidates.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => (s + 1) % candidates.length); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => (s - 1 + candidates.length) % candidates.length); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(candidates[sel]); return }
      if (e.key === 'Escape') { setMention(null); return }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send()
    }
  }

  return (
    <div className="relative px-4 pb-3 pt-1">
      {mention && candidates.length > 0 && (
        <div className="absolute bottom-full left-4 mb-1 w-44 rounded-md border border-gray-200 bg-white shadow-md py-1 z-10">
          {candidates.map((r, i) => (
            <button
              key={r}
              className={`w-full text-left px-3 py-1 text-[13px] ${i === sel ? 'bg-blue-50 text-blue-700' : 'hover:bg-gray-50'}`}
              onMouseDown={(e) => { e.preventDefault(); pick(r) }}
              onMouseEnter={() => setSel(i)}
            >
              @{r} <span className="text-gray-400">{roleName(r)}</span>
            </button>
          ))}
        </div>
      )}
      <textarea
        ref={ta}
        value={text}
        rows={2}
        placeholder={placeholder}
        className="w-full resize-none rounded-md border border-gray-300 px-3 py-2 text-[14px] focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-200"
        onChange={(e) => { setText(e.target.value); detectMention(e.target.value, e.target.selectionStart) }}
        onKeyDown={onKeyDown}
        onClick={(e) => detectMention(text, e.currentTarget.selectionStart)}
        disabled={busy}
      />
      <div className="flex justify-between text-[11px] text-gray-400 mt-0.5">
        <span>{err ? <span className="text-red-600">{err}</span> : 'Enter to send · Shift+Enter for a newline · @ to mention'}</span>
      </div>
    </div>
  )
}
