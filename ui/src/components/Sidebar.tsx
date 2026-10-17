import type { Feature, Project, ProjectSummary, TeamRole } from '../api'
import { ROLES, dotClass, meterClass, phaseChip, roleName } from '../roles'
import NewTaskForm from './NewTaskForm'

export default function Sidebar({
  projects,
  project,
  features,
  team,
  current,
  paused,
  inboxCount,
  onPickProject,
  onSelect,
  onNewTask,
  onPause,
}: {
  projects: ProjectSummary[]
  project: Project | null
  features: Feature[]
  team: TeamRole[]
  current: string
  paused: boolean
  inboxCount: number
  onPickProject: (id: string) => void
  onSelect: (id: string) => void
  onNewTask: (body: { text: string; starter: string }) => Promise<void>
  onPause: (paused: boolean) => void
}) {
  const byRole = Object.fromEntries(team.map((t) => [t.role, t]))

  const isHistory = (f: Feature) => !!f.archived || f.phase === 'done'
  const activity = (f: Feature) => f.last_activity ?? f.updated_at ?? f.created_at
  const active = features.filter((f) => !isHistory(f)).sort((a, b) => activity(b) - activity(a))
  const historyCount = features.filter(isHistory).length

  const row = (id: string, label: React.ReactNode, extra?: React.ReactNode) => (
    <button
      key={id}
      onClick={() => onSelect(id)}
      className={`w-full text-left px-3 py-1 rounded-md text-[13.5px] ${current === id ? 'bg-blue-100 text-blue-900 font-medium' : 'text-gray-700 hover:bg-gray-200/60'}`}
    >
      {label}
      {extra}
    </button>
  )

  const featureRow = (f: Feature) =>
    row(
      f.conversation_id,
      <span className="flex items-center gap-1.5 min-w-0">
        <span className="font-mono text-[11.5px] text-gray-500 shrink-0">{f.key}</span>
        <span className="truncate">{f.title}</span>
        <span className={`ml-auto shrink-0 text-[10.5px] rounded-full px-1.5 py-px font-medium ${phaseChip(f.phase)}`}>{f.archived ? 'archived' : f.phase}</span>
      </span>,
    )

  return (
    <aside className="w-[260px] shrink-0 bg-gray-50 border-r border-gray-200 flex flex-col h-full">
      <div className="p-3 border-b border-gray-200">
        <select
          value={project?.id || ''}
          onChange={(e) => onPickProject(e.target.value)}
          className="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-[13.5px] font-medium"
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {project && !project.is_git && <div className="mt-1 text-[11.5px] text-amber-700">not a git repo</div>}
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-3">
        <div>
          {row('dashboard', 'Dashboard')}
          {row(
            'inbox',
            <span className="flex items-center gap-2">
              <span>Inbox</span>
              {inboxCount > 0 && <span className="ml-auto rounded-full bg-blue-600 text-white text-[10.5px] font-semibold px-1.5 py-px leading-4">{inboxCount}</span>}
            </span>,
          )}
        </div>
        <div className="px-1">
          <NewTaskForm onNewTask={onNewTask} />
        </div>
        <div>
          <div className="px-3 text-[11px] uppercase tracking-wide text-gray-500 font-semibold mb-1">Channels</div>
          {row('general', '# general')}
          {row('standup', '# standup')}
        </div>
        <div>
          <div className="px-3 text-[11px] uppercase tracking-wide text-gray-500 font-semibold mb-1">Features</div>
          {active.length === 0 && <div className="px-3 text-xs text-gray-400">none yet</div>}
          {active.map(featureRow)}
          {row(
            'history',
            <span className="flex items-center gap-2 text-[12.5px]">
              <span>History</span>
              <span className="text-gray-400">({historyCount})</span>
            </span>,
          )}
        </div>
        <div>
          <div className="px-3 text-[11px] uppercase tracking-wide text-gray-500 font-semibold mb-1">Direct messages</div>
          {ROLES.map((r) => {
            const t = byRole[r]
            const pct = Math.min(1, Math.max(0, t?.context_pct || 0))
            return row(
              `dm-${r}`,
              <span className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass(t?.state || 'idle')}`} />
                <span>{roleName(r)}</span>
              </span>,
              <span className="block ml-4 mt-1 h-[3px] rounded bg-gray-200 overflow-hidden" title={t ? `context ${Math.round(pct * 100)}%` : undefined}>
                <span className={`block h-full ${meterClass(pct)}`} style={{ width: `${pct * 100}%` }} />
              </span>,
            )
          })}
        </div>
      </div>

      <label className="flex items-center gap-2 px-4 py-3 border-t border-gray-200 text-[13px] text-gray-700 cursor-pointer select-none">
        <input type="checkbox" checked={paused} onChange={(e) => onPause(e.target.checked)} />
        Pause project
      </label>
    </aside>
  )
}
