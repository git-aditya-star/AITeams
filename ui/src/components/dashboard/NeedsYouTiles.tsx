import { BadgeCheck, CircleHelp, GitPullRequest, History, MessageSquare, type LucideIcon } from 'lucide-react'
import type { Dashboard } from '../../api'
import { fmtAgo } from '../../api'
import NewTaskForm from '../NewTaskForm'
import StandupButton from '../StandupButton'
import { Button } from '../ui/button'
import { Tip } from '../ui/tooltip'

const TILES: [keyof Dashboard['needs_you'], LucideIcon, string, string][] = [
  ['questions', CircleHelp, 'question', 'questions'],
  ['approvals', BadgeCheck, 'approval', 'approvals'],
  ['commits', GitPullRequest, 'commit', 'commits'],
  ['messages', MessageSquare, 'message', 'messages'],
]

// F-18: what waits on the Master, as four tiles (zero ones greyed); a click opens the Inbox. New task and
// Standup sit at the row's end, with the last standup report behind a clock icon.
export default function NeedsYouTiles({
  dash,
  onSelect,
  onNewTask,
}: {
  dash: Dashboard
  onSelect: (id: string, messageId?: number) => void
  onNewTask: (body: { text: string; starter: string }) => Promise<void>
}) {
  const last = dash.last_standup?.state === 'done' ? dash.last_standup : null
  return (
    <section aria-label="Needs you" className="flex items-center gap-3 flex-wrap">
      {TILES.map(([k, Icon, one, many]) => {
        const v = dash.needs_you[k]
        const label = `${v} ${v === 1 ? one : many}`
        return (
          <Tip key={k} content={label}>
            <button
              data-tile={k}
              aria-label={label}
              onClick={() => onSelect('inbox')}
              className={`flex h-20 w-24 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border hover:bg-gray-50 ${
                v ? 'border-gray-300 text-gray-900' : 'border-gray-200 text-gray-300'
              }`}
            >
              <Icon className={`size-5 ${v ? 'text-blue-600' : ''}`} />
              <span className="text-title">{v}</span>
            </button>
          </Tip>
        )
      })}
      <div className="ml-auto flex items-center gap-2">
        <StandupButton />
        {last && (
          <Tip content={`last standup report · ${fmtAgo(last.ended_at ?? last.created_at)}`}>
            <Button
              variant="ghost"
              size="icon-xs"
              className="text-gray-500"
              aria-label="Last standup report"
              data-last-standup
              onClick={() => onSelect('standup', last.message_id ?? undefined)}
            >
              <History className="size-4" />
            </Button>
          </Tip>
        )}
        <NewTaskForm onNewTask={onNewTask} buttonClass="" />
      </div>
    </section>
  )
}
