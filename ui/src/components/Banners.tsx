import type { Question } from '../api'
import { roleName } from '../roles'
import { useAppSelector } from '../store'
import { selectPendingQuestions, selectUpgradeAvailable } from '../store/selectors'
import { Button } from './ui/button'

export default function Banners({ onAnswer }: { onAnswer: (q: Question) => void }) {
  const rateLimit = useAppSelector((s) => s.session.rateLimit)
  const paused = useAppSelector((s) => s.session.paused)
  const schemaAhead = useAppSelector((s) => !!s.session.project?.schema_ahead)
  const upgradeAvailable = useAppSelector(selectUpgradeAvailable)
  const pending = useAppSelector(selectPendingQuestions)
  return (
    <div className="shrink-0">
      {upgradeAvailable && (
        <div className="bg-gray-50 border-b border-gray-200 text-gray-700 px-4 py-1.5 text-meta">
          New commits since the running version, run <code className="font-mono text-code bg-gray-100 rounded-sm px-1">aiteams upgrade</code>
        </div>
      )}
      {schemaAhead && (
        <div className="bg-amber-50 border-b border-amber-200 text-amber-900 px-4 py-1.5 text-meta">
          This project's database is from a newer AITeams; upgrade again
        </div>
      )}
      {rateLimit && (
        <div className="bg-amber-50 border-b border-amber-200 text-amber-900 px-4 py-1.5 text-meta">
          Rate limited ({rateLimit.status}{rateLimit.rateLimitType ? `, ${rateLimit.rateLimitType}` : ''})
          {rateLimit.resetsAt ? ` · resets ${new Date(rateLimit.resetsAt * 1000).toLocaleString()}` : ''} — turns are held until it clears.
        </div>
      )}
      {paused && (
        <div className="bg-gray-100 border-b border-gray-200 text-gray-700 px-4 py-1.5 text-meta">
          Project paused — messages are stored but no role will be woken.
        </div>
      )}
      {pending.map((q) => (
        <div key={q.id} className="bg-blue-50 border-b border-blue-200 text-blue-900 px-4 py-1.5 text-meta flex items-center gap-3">
          <span className="truncate">
            <span className="font-semibold">{roleName(q.role)}</span> is asking: {q.payload?.questions?.[0]?.question}
          </span>
          <Button size="xs" className="ml-auto" onClick={() => onAnswer(q)}>
            Answer
          </Button>
        </div>
      ))}
    </div>
  )
}
