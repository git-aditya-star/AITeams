import { useState } from 'react'
import type { Question, QuestionItem } from '../api'
import { roleName } from '../roles'

function Item({ q, value, onChange }: { q: QuestionItem; value: string[]; onChange: (v: string[]) => void }) {
  const [other, setOther] = useState('')
  const labels = q.options.map((o) => o.label)
  const toggle = (label: string) => {
    if (q.multiSelect) onChange(value.includes(label) ? value.filter((v) => v !== label) : [...value, label])
    else onChange([label])
  }
  const otherSelected = value.length === 1 && !labels.includes(value[0])
  return (
    <div className="mb-3 last:mb-0">
      {q.header && <span className="inline-block text-[11px] font-medium rounded-full bg-blue-100 text-blue-800 px-2 py-px mb-1">{q.header}</span>}
      <div className="font-medium mb-1.5">{q.question}</div>
      <div className="flex flex-col gap-1">
        {q.options.map((o) => {
          const on = value.includes(o.label)
          return (
            <button
              key={o.label}
              type="button"
              onClick={() => toggle(o.label)}
              className={`text-left rounded-md border px-3 py-1.5 flex gap-2 items-start ${on ? 'border-blue-500 bg-white' : 'border-blue-200 bg-white/60 hover:bg-white'}`}
            >
              {q.multiSelect && <input type="checkbox" readOnly checked={on} className="mt-1" />}
              <span>
                <span className="font-semibold">{o.label}</span>
                {o.description && <span className="block text-xs text-gray-500">{o.description}</span>}
              </span>
            </button>
          )
        })}
        <input
          type="text"
          placeholder="Other…"
          value={otherSelected ? value[0] : other}
          onChange={(e) => { setOther(e.target.value); onChange(e.target.value ? [e.target.value] : []) }}
          className="rounded-md border border-blue-200 px-3 py-1.5 text-[13px] bg-white/60 focus:bg-white focus:outline-none focus:border-blue-400"
        />
      </div>
    </div>
  )
}

export default function QuestionCard({ q, onAnswer }: { q: Question; onAnswer: (answers: Record<string, string>) => Promise<void> }) {
  const [values, setValues] = useState<Record<string, string[]>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const items = q.payload?.questions || []

  if (q.state !== 'pending') {
    const outcome =
      q.state === 'answered'
        ? `Answered: ${Object.values(q.answers || {}).join(' · ') || '—'}`
        : 'Expired'
    return (
      <div className="mx-4 my-2 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-[13px] text-gray-500">
        <span className="font-medium text-gray-600">{roleName(q.role)} asked:</span> {items[0]?.question}
        <div className="mt-0.5">{outcome}</div>
      </div>
    )
  }

  const complete = items.every((it) => (values[it.question] || []).length > 0)
  const submit = async () => {
    const answers: Record<string, string> = {}
    items.forEach((it) => (answers[it.question] = (values[it.question] || []).join(', ')))
    setBusy(true)
    setErr(null)
    try {
      await onAnswer(answers)
    } catch (e: any) {
      setErr(e?.message || 'failed')
    } finally {
      setBusy(false)
    }
  }
  // Single-select, single-question: clicking an option answers immediately.
  const instant = items.length === 1 && !items[0].multiSelect

  return (
    <div className="mx-4 my-2 rounded-md border border-blue-300 bg-blue-50 px-3 py-2.5">
      <div className="text-xs text-blue-700 font-medium mb-1.5">{roleName(q.role)} is asking</div>
      {items.map((it) => (
        <Item
          key={it.question}
          q={it}
          value={values[it.question] || []}
          onChange={(v) => {
            const next = { ...values, [it.question]: v }
            setValues(next)
            if (instant && v.length && it.options.some((o) => o.label === v[0])) {
              setBusy(true)
              onAnswer({ [it.question]: v[0] }).catch((e) => setErr(e?.message || 'failed')).finally(() => setBusy(false))
            }
          }}
        />
      ))}
      <div className="flex items-center gap-2 mt-2">
        <button
          type="button"
          disabled={!complete || busy}
          onClick={submit}
          className="rounded-md bg-blue-600 text-white px-3 py-1 text-[13px] font-medium disabled:opacity-40 hover:bg-blue-700"
        >
          Submit
        </button>
        {err && <span className="text-xs text-red-600">{err}</span>}
      </div>
    </div>
  )
}
