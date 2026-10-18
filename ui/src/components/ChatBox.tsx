import { useLayoutEffect, useRef, useState } from 'react'
import { MENTIONABLE, mentionRole, roleName } from '../roles'
import { Command, CommandItem, CommandList } from './ui/command'
import { Popover, PopoverAnchor, PopoverContent } from './ui/popover'
import { Textarea } from './ui/textarea'

export default function ChatBox({ onSend, placeholder }: { onSend: (text: string) => Promise<void>; placeholder: string }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [mention, setMention] = useState<{ start: number; prefix: string } | null>(null)
  const [sel, setSel] = useState(0)
  const ta = useRef<HTMLTextAreaElement>(null)
  // Caret after an inserted mention, placed right after the commit so keys typed next land after it.
  const caretAfter = useRef<number | null>(null)

  const candidates = mention ? MENTIONABLE.filter((r) => r.startsWith(mention.prefix.toLowerCase())) : []

  const detectMention = (value: string, caret: number) => {
    const m = /(^|\s)@([\w-]*)$/.exec(value.slice(0, caret))
    if (m) {
      setMention({ start: caret - m[2].length - 1, prefix: m[2] })
      setSel(0)
    } else setMention(null)
  }

  const pick = (role: string) => {
    if (!mention) return
    const caret = ta.current?.selectionStart ?? text.length
    const next = `${text.slice(0, mention.start)}@${role} ${text.slice(caret)}`
    caretAfter.current = mention.start + role.length + 2
    setText(next)
    setMention(null)
  }
  useLayoutEffect(() => {
    const pos = caretAfter.current
    if (pos == null) return
    caretAfter.current = null
    ta.current?.setSelectionRange(pos, pos)
    ta.current?.focus()
  }, [text])

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

  // Focus stays in the textarea; the arrows/Enter/Tab here drive the picker's highlighted item.
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
    <div className="px-4 pb-3 pt-1">
      <Popover open={!!mention && candidates.length > 0} onOpenChange={(open) => !open && setMention(null)}>
        <PopoverAnchor asChild>
          <div>
            <Textarea
              ref={ta}
              value={text}
              rows={2}
              placeholder={placeholder}
              className="resize-none px-3 py-2 text-body focus-visible:ring-1 focus-visible:ring-blue-200"
              onChange={(e) => { setText(e.target.value); detectMention(e.target.value, e.target.selectionStart) }}
              onKeyDown={onKeyDown}
              onClick={(e) => detectMention(text, e.currentTarget.selectionStart)}
              disabled={busy}
            />
          </div>
        </PopoverAnchor>
        <PopoverContent
          side="top"
          align="start"
          className="w-44 p-0"
          onOpenAutoFocus={(e) => e.preventDefault()}
          onCloseAutoFocus={(e) => e.preventDefault()}
          onMouseDown={(e) => e.preventDefault()}
        >
          <Command shouldFilter={false} value={candidates[sel] ?? ''} onValueChange={(v) => setSel(Math.max(0, candidates.findIndex((r) => r === v)))} aria-label="Mention a role">
            <CommandList>
              {candidates.map((r) => (
                <CommandItem key={r} value={r} onSelect={() => pick(r)}>
                  @{r} <span className="text-gray-400">{roleName(mentionRole(r))}</span>
                </CommandItem>
              ))}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      <div className="flex justify-between text-caption text-gray-400 mt-0.5">
        <span>{err ? <span className="text-red-600">{err}</span> : 'Enter to send · Shift+Enter for a newline · @ to mention'}</span>
      </div>
    </div>
  )
}
