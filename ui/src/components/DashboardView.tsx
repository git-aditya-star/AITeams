import { useAppSelector } from '../store'
import { selectTeamByRole } from '../store/selectors'
import ActivitySpark from './dashboard/ActivitySpark'
import NeedsYouTiles from './dashboard/NeedsYouTiles'
import PhaseBar from './dashboard/PhaseBar'
import ShippedTimeline from './dashboard/ShippedTimeline'
import TeamRings from './dashboard/TeamRings'

// F-18: the Dashboard as pictures; words only in tooltips. Blocks whose data an older daemon doesn't send
// (activity_hourly, shipped.timeline) are left out.
export default function DashboardView({
  onSelect,
  onNewTask,
}: {
  onSelect: (id: string, messageId?: number) => void
  onNewTask: (body: { text: string; starter: string }) => Promise<void>
}) {
  const dash = useAppSelector((s) => s.dashboard)
  const byRole = useAppSelector(selectTeamByRole)
  if (!dash) return <div className="flex-1 px-4 py-6 text-gray-400 text-meta">Loading…</div>

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-8 text-meta">
      <NeedsYouTiles dash={dash} onSelect={onSelect} onNewTask={onNewTask} />
      {dash.activity_hourly && <ActivitySpark hours={dash.activity_hourly} />}
      <PhaseBar features={dash.features} onSelect={onSelect} />
      {dash.shipped?.timeline && <ShippedTimeline shipped={dash.shipped} onSelect={onSelect} />}
      <TeamRings byRole={byRole} />
    </div>
  )
}
