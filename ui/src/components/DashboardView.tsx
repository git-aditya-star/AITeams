import { useAppSelector } from '../store'
import { selectTeamByRole } from '../store/selectors'
import ActivitySpark, { activityHeadline } from './dashboard/ActivitySpark'
import NeedsYouTiles from './dashboard/NeedsYouTiles'
import PhaseBar from './dashboard/PhaseBar'
import ShippedTimeline from './dashboard/ShippedTimeline'
import TeamRings from './dashboard/TeamRings'
import { Card, CardContent, CardHeader } from './ui/card'

// F-18 v2: the Dashboard as a 12-column grid of titled cards: four KPI cards, then Activity (wide) + Features,
// then Shipped (wide) + Team. Below lg every card takes the full width. Standup and New task live in the page
// header (DashboardActions).
const KPI = 'col-span-12 lg:col-span-3'
const WIDE = 'col-span-12 lg:col-span-8'
const NARROW = 'col-span-12 lg:col-span-4'

function Block({
  id,
  title,
  headline,
  className,
  children,
}: {
  id: string
  title: string
  headline?: React.ReactNode
  className: string
  children: React.ReactNode
}) {
  return (
    <Card className={className} data-card={id}>
      <CardHeader title={title} headline={headline} />
      <CardContent>{children}</CardContent>
    </Card>
  )
}

const Missing = () => <div className="text-meta text-gray-400">Not available from this daemon; run aiteams upgrade.</div>

export default function DashboardView({ onSelect }: { onSelect: (id: string, messageId?: number) => void }) {
  const dash = useAppSelector((s) => s.dashboard)
  const byRole = useAppSelector(selectTeamByRole)
  if (!dash) return <div className="flex-1 px-4 py-6 text-gray-400 text-meta">Loading…</div>

  return (
    <div className="flex-1 overflow-y-auto p-4 text-meta">
      <div className="grid grid-cols-12 gap-4" data-dashboard-grid>
        <NeedsYouTiles dash={dash} onSelect={onSelect} className={KPI} />
        <Block
          id="activity"
          title="Activity · last 24h"
          headline={dash.activity_hourly && <span data-spark-headline>{activityHeadline(dash.activity_hourly)}</span>}
          className={WIDE}
        >
          {dash.activity_hourly ? <ActivitySpark hours={dash.activity_hourly} /> : <Missing />}
        </Block>
        <Block id="features" title="Features by phase" className={NARROW}>
          <PhaseBar features={dash.features} onSelect={onSelect} />
        </Block>
        <Block id="shipped" title="Shipped · last 14 days" className={WIDE}>
          {dash.shipped?.timeline ? <ShippedTimeline shipped={dash.shipped} onSelect={onSelect} /> : <Missing />}
        </Block>
        <Block id="team" title="Team" className={NARROW}>
          <TeamRings byRole={byRole} />
        </Block>
      </div>
    </div>
  )
}
