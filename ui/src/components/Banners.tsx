import type { Question, RateLimitInfo } from '../api'
import { roleName } from '../roles'

export default function Banners({
  rateLimit,
  paused,
  pending,
  upgradeAvailable,
  schemaAhead,
  onAnswer,
}: {
  rateLimit: RateLimitInfo | null
  paused: boolean
  pending: Question[]
  upgradeAvailable: boolean
  schemaAhead: boolean
  onAnswer: (q: Question) => void
}) {
  return (
    <div className="shrink-0">
      {upgradeAvailable && (
        <div className="bg-gray-50 border-b border-gray-200 text-gray-700 px-4 py-1.5 text-[13px]">
          New commits since the running version, run <code className="font-mono bg-gray-100 rounded px-1">aiteams upgrade</code>
        </div>
      )}
      {schemaAhead && (
        <div className="bg-amber-50 border-b border-amber-200 text-amber-900 px-4 py-1.5 text-[13px]">
          This project's database is from a newer AITeams; upgrade again
        </div>
      )}
      {rateLimit && (
        <div className="bg-amber-50 border-b border-amber-200 text-amber-900 px-4 py-1.5 text-[13px]">
          Rate limited ({rateLimit.status}{rateLimit.rateLimitType ? `, ${rateLimit.rateLimitType}` : ''})
          {rateLimit.resetsAt ? ` · resets ${new Date(rateLimit.resetsAt * 1000).toLocaleString()}` : ''} — turns are held until it clears.
        </div>
      )}
      {paused && (
        <div className="bg-gray-100 border-b border-gray-200 text-gray-700 px-4 py-1.5 text-[13px]">
          Project paused — messages are stored but no role will be woken.
        </div>
      )}
      {pending.map((q) => (
        <div key={q.id} className="bg-blue-50 border-b border-blue-200 text-blue-900 px-4 py-1.5 text-[13px] flex items-center gap-3">
          <span className="truncate">
            <span className="font-semibold">{roleName(q.role)}</span> is asking: {q.payload?.questions?.[0]?.question}
          </span>
          <button className="ml-auto shrink-0 rounded-md bg-blue-600 text-white px-2.5 py-0.5 text-xs font-medium hover:bg-blue-700" onClick={() => onAnswer(q)}>
            Answer
          </button>
        </div>
      ))}
    </div>
  )
}
