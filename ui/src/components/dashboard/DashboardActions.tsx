import { History } from 'lucide-react'
import { fmtAgo } from '../../api'
import { useAppSelector } from '../../store'
import NewTaskForm from '../NewTaskForm'
import StandupButton from '../StandupButton'
import { Button } from '../ui/button'
import { Tip } from '../ui/tooltip'

// F-18: the Dashboard header's right side: Standup, the last standup report (clock icon) and New task, all at the
// kit's default button height.
export default function DashboardActions({
  onSelect,
  onNewTask,
}: {
  onSelect: (id: string, messageId?: number) => void
  onNewTask: (body: { text: string; starter: string }) => Promise<void>
}) {
  const s = useAppSelector((st) => st.dashboard?.last_standup ?? null)
  const last = s?.state === 'done' ? s : null
  return (
    <div className="ml-auto flex items-center gap-2" data-dashboard-actions>
      <StandupButton size="default" />
      {last && (
        <Tip content={`last standup report · ${fmtAgo(last.ended_at ?? last.created_at)}`}>
          <Button
            variant="outline"
            size="icon"
            className="text-gray-500"
            aria-label="Last standup report"
            data-last-standup
            onClick={() => onSelect('standup', last.message_id ?? undefined)}
          >
            <History className="size-4" />
          </Button>
        </Tip>
      )}
      <NewTaskForm onNewTask={onNewTask} buttonClass="" size="default" />
    </div>
  )
}
