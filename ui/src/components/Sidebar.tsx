import { Fragment } from 'react'
import type { Feature, TeamLane } from '../api'
import { ROLES, contextNote, contextPct, dotClass, laneTip, meterClass, phaseChip, roleName } from '../roles'
import { useAppDispatch, useAppSelector } from '../store'
import { selectActiveFeatures, selectHistoryCount, selectInboxBadge, selectTeamByRole } from '../store/selectors'
import { setPaused } from '../store/thunks'
import AlertsMenu, { AlertHints } from './AlertsMenu'
import NewTaskForm from './NewTaskForm'
import StallChip from './StallChip'
import StuckTag from './StuckTag'
import { Badge } from './ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { Tip } from './ui/tooltip'

// F-48: under a role's DM row, one sub-row per active feature session (the strip's lanes); `onSelectLane` opens that session's Live view,
// and `currentLane` ('' = the role's main session) says which one the open DM shows.
export default function Sidebar({
  current,
  currentLane = '',
  onPickProject,
  onSelect,
  onSelectLane,
  onNewTask,
}: {
  current: string
  currentLane?: string
  onPickProject: (id: string) => void
  onSelect: (id: string) => void
  onSelectLane: (role: string, lane: string) => void
  onNewTask: (body: { text: string; starter: string }) => Promise<void>
}) {
  const dispatch = useAppDispatch()
  const projects = useAppSelector((s) => s.session.projects)
  const project = useAppSelector((s) => s.session.project)
  const paused = useAppSelector((s) => s.session.paused)
  const health = useAppSelector((s) => s.session.health)
  const byRole = useAppSelector(selectTeamByRole)
  const active = useAppSelector(selectActiveFeatures)
  const historyCount = useAppSelector(selectHistoryCount)
  const inboxCount = useAppSelector(selectInboxBadge)

  const row = (id: string, label: React.ReactNode, extra?: React.ReactNode, open = current === id) => (
    <button
      key={id}
      onClick={() => onSelect(id)}
      className={`w-full cursor-pointer text-left px-3 py-1 rounded-md text-meta ${open ? 'bg-blue-100 text-blue-900 font-medium' : 'text-gray-700 hover:bg-gray-200/60'}`}
    >
      {label}
      {extra}
    </button>
  )

  const laneRow = (r: string, l: TeamLane) => {
    const open = current === `dm-${r}` && currentLane === l.lane
    const pct = Math.round(contextPct(l) * 100)
    return (
      <Tip key={`${r}:${l.lane}`} content={laneTip(r, l)}>
        <button
          data-lane-row={`${r}:${l.lane}`}
          aria-label={`${l.lane} · ${roleName(r)} session ${pct}%`}
          onClick={() => onSelectLane(r, l.lane)}
          className={`w-full cursor-pointer flex items-center gap-2 text-left pl-7 pr-3 py-0.5 rounded-md text-caption ${open ? 'bg-blue-100 text-blue-900 font-medium' : 'text-gray-600 hover:bg-gray-200/60'}`}
        >
          <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass(l.state || 'idle')}`} />
          <span className="font-mono">{l.lane}</span>
          {l.stuck && <StuckTag />}
          <span className="ml-auto text-gray-500">{pct}%</span>
        </button>
      </Tip>
    )
  }

  const featureRow = (f: Feature) =>
    row(
      f.conversation_id,
      <span className="flex items-center gap-1.5 min-w-0">
        <span className="font-mono text-caption text-gray-500 shrink-0">{f.key}</span>
        <span className="truncate">{f.title}</span>
        <span className="ml-auto flex items-center gap-1 shrink-0">
          <StallChip stall={f.stall} compact />
          <Badge className={`px-1.5 ${phaseChip(f.phase)}`}>{f.archived ? 'archived' : f.phase}</Badge>
        </span>
      </span>,
    )

  return (
    <aside className="w-[260px] shrink-0 bg-gray-50 border-r border-gray-200 flex flex-col h-full">
      <div className="p-3 border-b border-gray-200">
        <Select value={project?.id || undefined} onValueChange={onPickProject}>
          <SelectTrigger aria-label="Project" className="w-full font-medium">
            <SelectValue placeholder="Project" />
          </SelectTrigger>
          <SelectContent>
            {projects.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {project && !project.is_git && <div className="mt-1 text-caption text-amber-700">not a git repo</div>}
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-3">
        <div>
          {row('dashboard', 'Dashboard')}
          {row(
            'inbox',
            <span className="flex items-center gap-2">
              <span>Inbox</span>
              {inboxCount > 0 && <Badge variant="default" className="ml-auto px-1.5 font-semibold">{inboxCount}</Badge>}
            </span>,
          )}
        </div>
        <div className="px-1">
          <NewTaskForm onNewTask={onNewTask} />
        </div>
        <div>
          <div className="px-3 text-caption uppercase tracking-wide text-gray-500 font-semibold mb-1">Channels</div>
          {row('general', '# general')}
          {row('standup', '# standup')}
        </div>
        <div>
          <div className="px-3 text-caption uppercase tracking-wide text-gray-500 font-semibold mb-1">Features</div>
          {active.length === 0 && <div className="px-3 text-caption text-gray-400">none yet</div>}
          {active.map(featureRow)}
          {row(
            'history',
            <span className="flex items-center gap-2">
              <span>History</span>
              <span className="text-gray-400">({historyCount})</span>
            </span>,
          )}
        </div>
        <div>
          <div className="px-3 text-caption uppercase tracking-wide text-gray-500 font-semibold mb-1">Direct messages</div>
          {ROLES.map((r) => {
            const t = byRole[r]
            const pct = contextPct(t)
            return (
              <Fragment key={r}>
                {row(
                  `dm-${r}`,
                  <span className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass(t?.state || 'idle')}`} />
                    <span>{roleName(r)}</span>
                  </span>,
                  <Tip content={t ? `context ${Math.round(pct * 100)}%${contextNote(t) ? ` · ${contextNote(t)}` : ''}` : undefined}>
                    <span data-ctx-meter={r} className="block ml-4 mt-1 h-[3px] rounded-sm bg-gray-200 overflow-hidden">
                      <span className={`block h-full ${meterClass(pct)}`} style={{ width: `${pct * 100}%` }} />
                    </span>
                  </Tip>,
                  current === `dm-${r}` && !currentLane, // the role row is its main session; a feature session has its own sub-row
                )}
                {(t?.lanes ?? []).map((l) => laneRow(r, l))}
              </Fragment>
            )
          })}
        </div>
      </div>

      <div className="border-t border-gray-200">
        <div className="flex items-center justify-between gap-2 px-4 py-3">
          <label className="flex items-center gap-2 text-meta text-gray-700 cursor-pointer select-none">
            <input type="checkbox" checked={paused} onChange={(e) => dispatch(setPaused(e.target.checked))} />
            Pause project
          </label>
          <AlertsMenu />
        </div>
        <AlertHints />
      </div>
      {health && (
        <Tip content={health.ref ? `${health.ref} from ${health.source}` : 'editable dev install'} side="top">
          <div className="self-start px-4 pb-2 text-caption text-gray-400 font-mono">
            v{health.version ?? '?'}{health.sha ? ` · ${health.sha.slice(0, 7)}` : ' · dev'}
          </div>
        </Tip>
      )}
    </aside>
  )
}
