import type { Reaction } from '../api'
import { roleName } from '../roles'
import { Tip } from './ui/tooltip'

// F-6: read-only reaction chips under a message. Only roles react (MCP `react`); the Master just sees them.
export default function Reactions({ reactions }: { reactions?: Reaction[] }) {
  const list = (reactions || []).filter((r) => r.roles?.length)
  if (!list.length) return null
  return (
    <div className="flex flex-wrap items-center gap-1 mt-1" data-reactions>
      {list.map((r) => (
        <Tip key={r.emoji} content={r.roles.map(roleName).join(', ')}>
          <span
            data-reaction={r.emoji}
            className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-muted px-1.5 py-px text-caption text-gray-700 select-none"
          >
            <span>{r.emoji}</span>
            <span className="tabular-nums">{r.roles.length}</span>
          </span>
        </Tip>
      ))}
    </div>
  )
}
