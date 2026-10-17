import { BadgeCheck, CircleHelp, GitPullRequest, MessageSquare, type LucideIcon } from 'lucide-react'
import type { Dashboard } from '../../api'
import { Card } from '../ui/card'

const TILES: [keyof Dashboard['needs_you'], LucideIcon, string, string][] = [
  ['questions', CircleHelp, 'Questions', 'question'],
  ['approvals', BadgeCheck, 'Approvals', 'approval'],
  ['commits', GitPullRequest, 'Commits', 'commit'],
  ['messages', MessageSquare, 'Messages', 'message'],
]

// F-18: what waits on the Master, as four KPI cards (icon, number, label); zero ones are greyed but keep their
// label. A click opens the Inbox. Each card is one cell of the Dashboard grid.
export default function NeedsYouTiles({ dash, onSelect, className }: { dash: Dashboard; onSelect: (id: string) => void; className: string }) {
  return (
    <>
      {TILES.map(([k, Icon, label, one]) => {
        const v = dash.needs_you[k]
        return (
          <Card key={k} className={`p-0 ${className}`} data-kpi={k}>
            <button
              data-tile={k}
              aria-label={`${v} ${v === 1 ? one : `${one}s`}`}
              onClick={() => onSelect('inbox')}
              className="flex h-full w-full cursor-pointer items-center gap-3 rounded-lg p-4 text-left hover:bg-gray-50"
            >
              <span className={`flex size-10 shrink-0 items-center justify-center rounded-md ${v ? 'bg-blue-50 text-blue-600' : 'bg-gray-50 text-gray-300'}`}>
                <Icon className="size-5" />
              </span>
              <span className="flex flex-col">
                <span data-tile-value className={`text-title ${v ? 'text-gray-900' : 'text-gray-300'}`}>
                  {v}
                </span>
                <span data-tile-label className={`text-meta ${v ? 'text-gray-600' : 'text-gray-400'}`}>
                  {label}
                </span>
              </span>
            </button>
          </Card>
        )
      })}
    </>
  )
}
