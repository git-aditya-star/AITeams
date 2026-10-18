import { useEffect, useState } from 'react'
import type { Approval, Feature } from '../api'
import { api, fmtAgo } from '../api'
import { phaseChip } from '../roles'
import { useAppDispatch, useAppSelector } from '../store'
import { selectPid } from '../store/selectors'
import { patchFeature } from '../store/thunks'
import ApprovalCard from './ApprovalCard'
import { Badge } from './ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs'

// F-25: the outcomes an approval can end with; `all` is the filter's "no filter".
const OUTCOMES = [
  { value: 'all', label: 'All' },
  { value: 'approved', label: 'Approved' },
  { value: 'changes_requested', label: 'Changes requested' },
  { value: 'rejected', label: 'Rejected' },
] as const

export default function HistoryView({ onOpen }: { onOpen: (conversationId: string) => void }) {
  const dispatch = useAppDispatch()
  const pid = useAppSelector(selectPid)
  const [tab, setTab] = useState('features')
  const [rows, setRows] = useState<Feature[] | null>(null)
  const [approvals, setApprovals] = useState<Approval[] | null>(null)
  const [outcome, setOutcome] = useState<string>('all')
  const [err, setErr] = useState<string | null>(null)
  const load = () => api.featuresHistory(pid).then(setRows).catch((e) => setErr(e.message))
  useEffect(() => {
    if (!pid) return
    setRows(null)
    void load()
  }, [pid])
  // The approvals are fetched each time their tab opens, so a decision made meanwhile shows up. Pending ones belong in
  // the Inbox, so they are dropped here.
  useEffect(() => {
    if (!pid || tab !== 'approvals') return
    api
      .approvals(pid, 'all')
      .then((list) => setApprovals(list.filter((a) => a.state !== 'pending')))
      .catch((e) => setErr(e.message))
  }, [pid, tab])

  const reopen = async (f: Feature, value: string) => {
    try {
      await dispatch(patchFeature(f.id, value === 'unarchive' ? { archived: false } : { phase: value }))
      await load()
    } catch (e: any) {
      setErr(e?.message || 'failed')
    }
  }

  const features = () => {
    if (!rows) return <div className="py-6 text-gray-400 text-meta">{err || 'Loading…'}</div>
    if (!rows.length) return <div className="py-6 text-gray-500 text-meta">No finished features yet.</div>
    return (
      <table className="w-full text-meta">
        <thead className="text-caption text-gray-400 text-left">
          <tr>
            <th className="font-medium pb-1 pr-3">feature</th>
            <th className="font-medium pb-1 pr-3">phase</th>
            <th className="font-medium pb-1 pr-3">last activity</th>
            <th className="font-medium pb-1">reopen</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((f) => (
            <tr key={f.id} className="border-t border-gray-100 hover:bg-gray-50">
              <td className="py-1.5 pr-3 max-w-[420px]">
                <button onClick={() => onOpen(f.conversation_id)} className="flex cursor-pointer items-center gap-1.5 min-w-0 max-w-full text-left">
                  <span className="font-mono text-caption text-gray-500 shrink-0">{f.key}</span>
                  <span className="truncate hover:underline">{f.title}</span>
                </button>
              </td>
              <td className="py-1.5 pr-3">
                <span className="inline-flex gap-1">
                  <Badge className={`px-1.5 ${phaseChip(f.phase)}`}>{f.phase}</Badge>
                  {!!f.archived && <Badge className="px-1.5">archived</Badge>}
                </span>
              </td>
              <td className="py-1.5 pr-3 text-gray-500">{fmtAgo(f.last_activity ?? f.updated_at)}</td>
              <td className="py-1.5">
                {/* value stays empty: the trigger always reads "reopen…", picking an item acts at once */}
                <Select value="" onValueChange={(v) => void reopen(f, v)}>
                  <SelectTrigger size="xs" aria-label={`Reopen ${f.key}`}>
                    <SelectValue placeholder="reopen…" />
                  </SelectTrigger>
                  <SelectContent>
                    {!!f.archived && <SelectItem value="unarchive">unarchive</SelectItem>}
                    {f.phase === 'done' && <SelectItem value="build">phase → build</SelectItem>}
                  </SelectContent>
                </Select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  const approvalsList = () => {
    if (!approvals) return <div className="py-6 text-gray-400 text-meta">{err || 'Loading…'}</div>
    const shown = outcome === 'all' ? approvals : approvals.filter((a) => a.state === outcome)
    return (
      <div className="flex flex-col gap-1">
        <label className="flex items-center gap-1.5 text-caption text-gray-500">
          outcome
          <Select value={outcome} onValueChange={setOutcome}>
            <SelectTrigger size="xs" aria-label="Outcome">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {OUTCOMES.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        {shown.length === 0 ? (
          <div data-approvals-empty className="py-6 text-gray-500 text-meta">
            No approvals with this outcome.
          </div>
        ) : (
          <div data-approvals-list className="max-w-3xl">
            {shown.map((a) => (
              <ApprovalCard key={a.id} a={a} showFeature />
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4">
      {err && rows && <div className="text-caption text-red-600 mb-2">{err}</div>}
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="features">Features</TabsTrigger>
          <TabsTrigger value="approvals">Approvals</TabsTrigger>
        </TabsList>
        <TabsContent value="features">{features()}</TabsContent>
        <TabsContent value="approvals">{approvalsList()}</TabsContent>
      </Tabs>
    </div>
  )
}
