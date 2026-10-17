import { useEffect, useState } from 'react'
import type { Feature } from '../api'
import { api, fmtAgo } from '../api'
import { phaseChip } from '../roles'
import { useAppDispatch, useAppSelector } from '../store'
import { selectPid } from '../store/selectors'
import { patchFeature } from '../store/thunks'
import { Badge } from './ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'

export default function HistoryView({ onOpen }: { onOpen: (conversationId: string) => void }) {
  const dispatch = useAppDispatch()
  const pid = useAppSelector(selectPid)
  const [rows, setRows] = useState<Feature[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = () => api.featuresHistory(pid).then(setRows).catch((e) => setErr(e.message))
  useEffect(() => {
    if (!pid) return
    setRows(null)
    void load()
  }, [pid])

  const reopen = async (f: Feature, value: string) => {
    try {
      await dispatch(patchFeature(f.id, value === 'unarchive' ? { archived: false } : { phase: value }))
      await load()
    } catch (e: any) {
      setErr(e?.message || 'failed')
    }
  }

  if (!rows) return <div className="flex-1 px-4 py-6 text-gray-400 text-meta">{err || 'Loading…'}</div>
  if (!rows.length) return <div className="flex-1 px-4 py-6 text-gray-500 text-meta">No finished features yet.</div>
  return (
    <div className="flex-1 overflow-y-auto px-4 py-4">
      {err && <div className="text-caption text-red-600 mb-2">{err}</div>}
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
    </div>
  )
}
