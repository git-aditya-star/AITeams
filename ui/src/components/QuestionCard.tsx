import { useState } from 'react'
import type { Question, QuestionItem } from '../api'
import { roleName, stateChip, stateLabel } from '../roles'
import { useAppDispatch } from '../store'
import { answerQuestion } from '../store/thunks'
import Markdown from './Markdown'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Input } from './ui/input'

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
      {q.header && <Badge className="mb-1 bg-blue-100 text-blue-800">{q.header}</Badge>}
      <div className="font-medium mb-1.5">{q.question}</div>
      <div className="flex flex-col gap-1">
        {q.options.map((o) => {
          const on = value.includes(o.label)
          return (
            <button
              key={o.label}
              type="button"
              onClick={() => toggle(o.label)}
              className={`cursor-pointer text-left rounded-md border px-3 py-1.5 flex gap-2 items-start ${on ? 'border-blue-500 bg-white' : 'border-blue-200 bg-white/60 hover:bg-white'}`}
            >
              {q.multiSelect && <input type="checkbox" readOnly checked={on} className="mt-1" />}
              <span>
                <span className="font-semibold">{o.label}</span>
                {o.description && <span className="block text-caption text-gray-500">{o.description}</span>}
              </span>
            </button>
          )
        })}
        <Input
          type="text"
          placeholder="Other…"
          value={otherSelected ? value[0] : other}
          onChange={(e) => { setOther(e.target.value); onChange(e.target.value ? [e.target.value] : []) }}
          className="h-auto border-blue-200 px-3 py-1.5 bg-white/60 focus:bg-white"
        />
      </div>
    </div>
  )
}

// F-10: the badge for a non-blocking card's purpose (plain asks get none).
const PURPOSE: Record<string, string> = { interview: 'Interview', concern: 'Concern', a_vs_b: 'A vs B', brainstorm: 'Brainstorm' }

// Purpose badge + intro, shown on cards (open and closed).
function CardHead({ q }: { q: Question }) {
  const purpose = q.purpose ? PURPOSE[q.purpose] : null
  if (q.kind !== 'card' || (!purpose && !q.intro)) return null
  return (
    <div className="mb-2">
      {purpose && <Badge className="mb-1 bg-violet-100 text-violet-800" data-card-purpose>{purpose}</Badge>}
      {q.intro && <Markdown text={q.intro} />}
    </div>
  )
}

// `embedded`: rendered inside its MessageRow (no side margins); otherwise in the Inbox or a DM footer.
export default function QuestionCard({ q, embedded = false }: { q: Question; embedded?: boolean }) {
  const dispatch = useAppDispatch()
  const onAnswer = (answers: Record<string, string>) => dispatch(answerQuestion(q, answers))
  const [values, setValues] = useState<Record<string, string[]>>({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const items = q.payload?.questions || []

  const box = embedded ? 'my-2 max-w-2xl' : 'mx-4 my-2'

  if (q.state !== 'pending') {
    if (q.kind === 'card') {
      // Cards keep every question visible once closed: question → answer, or who withdrew it.
      return (
        <div className={`${box} rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-meta text-gray-600`} data-question-card={q.id} data-state={q.state}>
          <CardHead q={q} />
          {q.state === 'withdrawn' ? (
            <div data-withdrawn className="flex items-center gap-2 text-gray-600">
              <Badge className={stateChip('withdrawn')}>{stateLabel('withdrawn')}</Badge>
              {q.note ? `Withdrawn: ${q.note}` : `Withdrawn by ${roleName(q.role)}`}
            </div>
          ) : (
            items.map((it) => (
              <div key={it.question} className="mb-1 last:mb-0">
                <span className="text-gray-500">{it.question}</span> → <span className="font-medium">{q.answers?.[it.question] || '—'}</span>
              </div>
            ))
          )}
        </div>
      )
    }
    const outcome =
      q.state === 'answered'
        ? `Answered: ${Object.values(q.answers || {}).join(' · ') || '—'}`
        : 'Expired'
    return (
      <div className={`${box} rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-meta text-gray-500`}>
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
    <div className={`${box} rounded-md border border-blue-300 bg-blue-50 px-3 py-2.5`} data-question-card={q.id} data-state={q.state}>
      <div className="text-caption text-blue-700 font-medium mb-1.5">{roleName(q.role)} is asking</div>
      <CardHead q={q} />
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
        <Button type="button" size="sm" disabled={!complete || busy} onClick={submit}>
          Submit
        </Button>
        {err && <span className="text-caption text-red-600">{err}</span>}
      </div>
    </div>
  )
}
