import { Check, ChevronRight, Clock, RotateCw, Square, type LucideIcon } from 'lucide-react'
import type { Activity, DashboardFeature } from '../api'
import { fmtAgo, fmtTime } from '../api'
import { PHASES, ROLES, dotClass, meterClass, phaseChip, roleName, stateWord } from '../roles'
import Avatar from './Avatar'
import NewTaskForm from './NewTaskForm'
import RoleActions from './RoleActions'
import { useAppSelector } from '../store'
import { selectTeamByRole } from '../store/selectors'
import StandupButton from './StandupButton'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Tip } from './ui/tooltip'

const ACTIVITY_ICON: Record<string, LucideIcon> = {
  feature_created: ChevronRight,
  phase: ChevronRight,
  approval: Check,
  commit: Check,
  handoff: RotateCw,
  fresh_session: RotateCw,
  fresh_start: RotateCw,
  standup: Clock,
  stopped: Square,
  interrupted: Square,
}

function Heading({ children }: { children: React.ReactNode }) {
  return <div className="text-caption uppercase tracking-wide text-gray-500 font-semibold mb-1.5">{children}</div>
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
  onSelect,
  onNewTask,
}: {
  onSelect: (id: string, messageId?: number) => void
  onNewTask: (body: { text: string; starter: string }) => Promise<void>
}) {
  const dash = useAppSelector((s) => s.dashboard)
  const byRole = useAppSelector(selectTeamByRole)
  if (!dash) return <div className="flex-1 px-4 py-6 text-gray-400 text-meta">Loading…</div>
  const n = dash.needs_you
  const counters: [string, number][] = [
    ['questions', n.questions],
    ['approvals', n.approvals],
    ['commits', n.commits],
    ['messages', n.messages],
  ]

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-6 text-meta">
      <section>
        <Heading>Needs you</Heading>
        <div className="flex items-center gap-4 flex-wrap">
          {counters.map(([k, v]) => (
            <button key={k} onClick={() => onSelect('inbox')} className="flex cursor-pointer items-baseline gap-1.5 hover:bg-gray-50 rounded-md px-2 py-1">
              <span className={`text-title ${v ? 'text-gray-900' : 'text-gray-400'}`}>{v}</span>
              <span className="text-gray-500">{k}</span>
            </button>
          ))}
          <div className="ml-auto">
            <NewTaskForm onNewTask={onNewTask} buttonClass="" />
          </div>
        </div>
      </section>

      <section>
        <Heading>Features</Heading>
        <div className="flex gap-3 text-caption text-gray-500 mb-2" data-phase-counts>
          {PHASES.filter((p) => p !== 'done').map((p) => (
            <span key={p}>
              <span className="font-semibold text-gray-700">{dash.phase_counts[p] ?? 0}</span> {p}
            </span>
          ))}
        </div>
        {dash.features.length === 0 ? (
          <div className="text-gray-400 text-caption">no active features</div>
        ) : (
          <table className="w-full text-meta">
            <thead className="text-caption text-gray-400 text-left">
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
                    <button onClick={() => onSelect(f.conversation_id)} className="flex cursor-pointer items-center gap-1.5 min-w-0 max-w-full text-left">
                      <span className="font-mono text-caption text-gray-500 shrink-0">{f.key}</span>
                      <span className="truncate hover:underline">{f.title}</span>
                    </button>
                  </td>
                  <td className="py-1.5 pr-3">
                    <Badge className={`px-1.5 ${phaseChip(f.phase)}`}>{f.phase}</Badge>
                  </td>
                  <td className="py-1.5 pr-3 text-gray-600">{f.owner ? roleName(f.owner) : <span className="text-gray-300">—</span>}</td>
                  <td className="py-1.5 pr-3">
                    {f.waits_on ? (
                      <Tip content={f.waits_on.since ? `${roleName(f.waits_on.who)} since ${fmtAgo(f.waits_on.since)}` : roleName(f.waits_on.who)}>
                        <span className="inline-flex items-center gap-1.5 text-gray-600">
                          <Avatar author={f.waits_on.who} size={18} tip={false} />
                          {f.waits_on.kind}
                        </span>
                      </Tip>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 text-gray-500">{fmtAgo(f.last_activity ?? f.updated_at)}</td>
                  <td className="py-1.5">{f.stale && <Badge variant="destructive" className="px-1.5">stale</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {dash.shipped && (
        <section data-shipped>
          <div className="flex items-baseline gap-3">
            <Heading>Shipped</Heading>
            <span className="text-caption text-gray-500">
              this week <span className="font-semibold text-gray-700" data-shipped-week>{dash.shipped.week}</span>
            </span>
            <span className="text-caption text-gray-500">
              total <span className="font-semibold text-gray-700" data-shipped-total>{dash.shipped.total}</span>
            </span>
          </div>
          {dash.shipped.recent.length === 0 ? (
            <div className="text-gray-400 text-caption">nothing shipped yet</div>
          ) : (
            <ul>
              {dash.shipped.recent.map((f) => (
                <li key={f.id} data-shipped-row={f.key} className="border-t border-gray-100 first:border-t-0">
                  <button onClick={() => onSelect(f.conversation_id)} className="flex w-full cursor-pointer items-baseline gap-2 py-1.5 text-left hover:bg-gray-50">
                    <span className="font-mono text-code text-gray-500 shrink-0">{f.key}</span>
                    <span className="text-body shrink-0 max-w-[40%] truncate hover:underline">{f.title}</span>
                    {f.summary && (
                      <Tip content={<span className="whitespace-pre-wrap">{f.summary}</span>}>
                        <span className="min-w-0 truncate text-meta text-gray-500" data-shipped-summary>
                          {f.summary}
                        </span>
                      </Tip>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

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
                    <Button variant="link" size="inline" className="text-meta font-normal" onClick={() => onSelect(cur.conversation_id)}>
                      {cur.feature_key || cur.label}
                    </Button>
                  ) : (
                    <span className="text-gray-300">—</span>
                  )}
                </span>
                <span className="flex items-center gap-1.5 w-[120px]">
                  <span className="block flex-1 h-[4px] rounded-sm bg-gray-200 overflow-hidden">
                    <span className={`block h-full ${meterClass(pct)}`} style={{ width: `${pct * 100}%` }} />
                  </span>
                  <span className="text-caption text-gray-500 w-8 text-right">{Math.round(pct * 100)}%</span>
                </span>
                <span className="text-caption text-gray-500">session #{t?.session_seq ?? '—'}</span>
                <span className="text-caption text-gray-500">fresh starts {t?.fresh_starts ?? 0}</span>
                <span className="ml-auto">
                  <RoleActions role={r} />
                </span>
              </div>
            )
          })}
        </div>
      </section>

      <section>
        <Heading>Activity</Heading>
        {dash.activity.length === 0 ? (
          <div className="text-gray-400 text-caption">nothing yet</div>
        ) : (
          <div className="divide-y divide-gray-100">
            {dash.activity.map((a: Activity) => {
              const Icon = ACTIVITY_ICON[a.kind] || ChevronRight
              return (
                <div key={a.id} className="flex items-center gap-2 py-1 text-meta">
                  <Tip content={new Date(a.ts * 1000).toLocaleString()}>
                    <span className="text-caption text-gray-400 w-10 shrink-0">{fmtTime(a.ts)}</span>
                  </Tip>
                  <Icon size={13} className="text-gray-400" />
                  {a.role && <Avatar author={a.role} size={18} />}
                  <span className="truncate text-gray-700" title={a.text}>{a.text}</span>
                  {a.conversation_id && (
                    <Button variant="link" size="inline" className="ml-auto text-caption font-normal" onClick={() => onSelect(a.conversation_id!)}>
                      open
                    </Button>
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
          <StandupButton />
          {dash.last_standup && dash.last_standup.state === 'done' && (
            <Button variant="link" size="inline" className="text-caption font-normal" onClick={() => onSelect('standup', dash.last_standup!.message_id ?? undefined)}>
              last report · {fmtAgo(dash.last_standup.ended_at ?? dash.last_standup.created_at)}
            </Button>
          )}
        </div>
      </section>
    </div>
  )
}
