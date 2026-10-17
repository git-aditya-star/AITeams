import type { Activity, Dashboard, DashboardFeature, Standup, TeamRole } from '../api'
import { fmtAgo, fmtTime } from '../api'
import { PHASES, ROLES, dotClass, meterClass, phaseChip, roleName, stateWord } from '../roles'
import Avatar from './Avatar'
import NewTaskForm from './NewTaskForm'
import RoleActions from './RoleActions'
import StandupButton from './StandupButton'
import { Check, Chevron, Clock, Refresh, Stop } from './icons'

const ACTIVITY_ICON: Record<string, typeof Check> = {
  feature_created: Chevron,
  phase: Chevron,
  approval: Check,
  commit: Check,
  handoff: Refresh,
  fresh_session: Refresh,
  fresh_start: Refresh,
  standup: Clock,
  stopped: Stop,
  interrupted: Stop,
}

function Heading({ children }: { children: React.ReactNode }) {
  return <div className="text-[11px] uppercase tracking-wide text-gray-500 font-semibold mb-1.5">{children}</div>
}

const sortFeatures = (fs: DashboardFeature[]) =>
  [...fs].sort((a, b) => {
    if (a.stale !== b.stale) return a.stale ? -1 : 1
    const am = a.waits_on?.who === 'master' ? 1 : 0
    const bm = b.waits_on?.who === 'master' ? 1 : 0
    if (am !== bm) return bm - am
    return (b.last_activity ?? b.updated_at) - (a.last_activity ?? a.updated_at)
  })

export default function DashboardView({
  dash,
  team,
  onSelect,
  onNewTask,
  onStop,
  onFreshStart,
  onStandup,
}: {
  dash: Dashboard | null
  team: TeamRole[]
  onSelect: (id: string, messageId?: number) => void
  onNewTask: (body: { text: string; starter: string }) => Promise<void>
  onStop: (role: string) => Promise<void>
  onFreshStart: (role: string, handoff: boolean) => Promise<void>
  onStandup: () => Promise<Standup | { status: string }>
}) {
  if (!dash) return <div className="flex-1 px-4 py-6 text-gray-400 text-[13px]">Loading…</div>
  const byRole = Object.fromEntries(team.map((t) => [t.role, t]))
  const n = dash.needs_you
  const counters: [string, number][] = [
    ['questions', n.questions],
    ['approvals', n.approvals],
    ['commits', n.commits],
    ['messages', n.messages],
  ]
  const link = 'text-blue-600 hover:underline'

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-6 text-[13px]">
      <section>
        <Heading>Needs you</Heading>
        <div className="flex items-center gap-4 flex-wrap">
          {counters.map(([k, v]) => (
            <button key={k} onClick={() => onSelect('inbox')} className="flex items-baseline gap-1.5 hover:bg-gray-50 rounded-md px-2 py-1">
              <span className={`text-lg font-semibold ${v ? 'text-gray-900' : 'text-gray-400'}`}>{v}</span>
              <span className="text-gray-500">{k}</span>
            </button>
          ))}
          <div className="ml-auto min-w-[260px]">
            <NewTaskForm onNewTask={onNewTask} buttonClass="rounded-md bg-blue-600 text-white px-3 py-1.5 text-[13px] font-medium hover:bg-blue-700" />
          </div>
        </div>
      </section>

      <section>
        <Heading>Features</Heading>
        <div className="flex gap-3 text-xs text-gray-500 mb-2">
          {PHASES.map((p) => (
            <span key={p}>
              <span className="font-semibold text-gray-700">{dash.phase_counts[p] ?? 0}</span> {p}
            </span>
          ))}
        </div>
        {dash.features.length === 0 ? (
          <div className="text-gray-400 text-xs">no active features</div>
        ) : (
          <table className="w-full text-[12.5px]">
            <thead className="text-[11px] text-gray-400 text-left">
              <tr>
                <th className="font-medium pb-1 pr-3">feature</th>
                <th className="font-medium pb-1 pr-3">phase</th>
                <th className="font-medium pb-1 pr-3">owner</th>
                <th className="font-medium pb-1 pr-3">waits on</th>
                <th className="font-medium pb-1 pr-3">last activity</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sortFeatures(dash.features).map((f) => (
                <tr key={f.id} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="py-1.5 pr-3 max-w-[360px]">
                    <button onClick={() => onSelect(f.conversation_id)} className="flex items-center gap-1.5 min-w-0 max-w-full text-left">
                      <span className="font-mono text-[11.5px] text-gray-500 shrink-0">{f.key}</span>
                      <span className="truncate hover:underline">{f.title}</span>
                    </button>
                  </td>
                  <td className="py-1.5 pr-3">
                    <span className={`text-[10.5px] rounded-full px-1.5 py-px font-medium ${phaseChip(f.phase)}`}>{f.phase}</span>
                  </td>
                  <td className="py-1.5 pr-3 text-gray-600">{f.owner ? roleName(f.owner) : <span className="text-gray-300">—</span>}</td>
                  <td className="py-1.5 pr-3">
                    {f.waits_on ? (
                      <span className="inline-flex items-center gap-1.5 text-gray-600" title={f.waits_on.since ? `since ${fmtAgo(f.waits_on.since)}` : undefined}>
                        <Avatar author={f.waits_on.who} size={18} />
                        {f.waits_on.kind}
                      </span>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 text-gray-500">{fmtAgo(f.last_activity ?? f.updated_at)}</td>
                  <td className="py-1.5">{f.stale && <span className="text-[10.5px] rounded-full px-1.5 py-px font-medium bg-red-50 text-red-700">stale</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <Heading>Team</Heading>
        <div className="divide-y divide-gray-100">
          {ROLES.map((r) => {
            const t = byRole[r]
            const pct = Math.min(1, Math.max(0, t?.context_pct || 0))
            const cur = t?.current
            return (
              <div key={r} className="flex items-center gap-3 py-1.5 flex-wrap">
                <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass(t?.state || 'idle')}`} />
                <span className="font-medium w-[72px]">{roleName(r)}</span>
                <span className="text-gray-500 w-[64px]">{stateWord(t)}</span>
                <span className="w-[160px] truncate">
                  {cur ? (
                    <button onClick={() => onSelect(cur.conversation_id)} className={link}>
                      {cur.feature_key || cur.label}
                    </button>
                  ) : (
                    <span className="text-gray-300">—</span>
                  )}
                </span>
                <span className="flex items-center gap-1.5 w-[120px]" title={`context ${Math.round(pct * 100)}%`}>
                  <span className="block flex-1 h-[4px] rounded bg-gray-200 overflow-hidden">
                    <span className={`block h-full ${meterClass(pct)}`} style={{ width: `${pct * 100}%` }} />
                  </span>
                  <span className="text-xs text-gray-500 w-8 text-right">{Math.round(pct * 100)}%</span>
                </span>
                <span className="text-xs text-gray-500">session #{t?.session_seq ?? '—'}</span>
                <span className="text-xs text-gray-500">fresh starts {t?.fresh_starts ?? 0}</span>
                <span className="ml-auto">
                  <RoleActions t={t} onStop={() => onStop(r)} onFreshStart={(h) => onFreshStart(r, h)} />
                </span>
              </div>
            )
          })}
        </div>
      </section>

      <section>
        <Heading>Activity</Heading>
        {dash.activity.length === 0 ? (
          <div className="text-gray-400 text-xs">nothing yet</div>
        ) : (
          <div className="divide-y divide-gray-100">
            {dash.activity.map((a: Activity) => {
              const Icon = ACTIVITY_ICON[a.kind] || Chevron
              return (
                <div key={a.id} className="flex items-center gap-2 py-1 text-[12.5px]">
                  <span className="text-xs text-gray-400 w-10 shrink-0" title={new Date(a.ts * 1000).toLocaleString()}>
                    {fmtTime(a.ts)}
                  </span>
                  <Icon size={13} className="text-gray-400" />
                  {a.role && <Avatar author={a.role} size={18} />}
                  <span className="truncate text-gray-700">{a.text}</span>
                  {a.conversation_id && (
                    <button onClick={() => onSelect(a.conversation_id!)} className={`ml-auto shrink-0 text-xs ${link}`}>
                      open
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

      <section>
        <Heading>Standup</Heading>
        <div className="flex items-center gap-3 flex-wrap">
          <StandupButton standup={dash.standup} onStart={onStandup} />
          {dash.last_standup && dash.last_standup.state === 'done' && (
            <button onClick={() => onSelect('standup', dash.last_standup!.message_id ?? undefined)} className={`text-xs ${link}`}>
              last report · {fmtAgo(dash.last_standup.ended_at ?? dash.last_standup.created_at)}
            </button>
          )}
        </div>
      </section>
    </div>
  )
}
