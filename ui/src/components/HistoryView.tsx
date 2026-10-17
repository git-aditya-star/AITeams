import { useEffect, useState } from 'react'
import type { Feature } from '../api'
import { api, fmtAgo } from '../api'
import { phaseChip } from '../roles'

export default function HistoryView({
  pid,
  onOpen,
  onPatch,
}: {
  pid: string
  onOpen: (conversationId: string) => void
  onPatch: (id: number, patch: { phase?: string; archived?: boolean }) => Promise<void>
}) {
  const [rows, setRows] = useState<Feature[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = () => api.featuresHistory(pid).then(setRows).catch((e) => setErr(e.message))
  useEffect(() => {
    setRows(null)
    void load()
  }, [pid])

  const reopen = async (f: Feature, value: string) => {
    if (!value) return
    try {
      await onPatch(f.id, value === 'unarchive' ? { archived: false } : { phase: value })
      await load()
    } catch (e: any) {
      setErr(e?.message || 'failed')
    }
  }

  if (!rows) return <div className="flex-1 px-4 py-6 text-gray-400 text-[13px]">{err || 'Loading…'}</div>
  if (!rows.length) return <div className="flex-1 px-4 py-6 text-gray-500 text-[13px]">No finished features yet.</div>
  return (
    <div className="flex-1 overflow-y-auto px-4 py-4">
      {err && <div className="text-xs text-red-600 mb-2">{err}</div>}
      <table className="w-full text-[12.5px]">
        <thead className="text-[11px] text-gray-400 text-left">
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
                <button onClick={() => onOpen(f.conversation_id)} className="flex items-center gap-1.5 min-w-0 max-w-full text-left">
                  <span className="font-mono text-[11.5px] text-gray-500 shrink-0">{f.key}</span>
                  <span className="truncate hover:underline">{f.title}</span>
                </button>
              </td>
              <td className="py-1.5 pr-3">
                <span className="inline-flex gap-1">
                  <span className={`text-[10.5px] rounded-full px-1.5 py-px font-medium ${phaseChip(f.phase)}`}>{f.phase}</span>
                  {!!f.archived && <span className="text-[10.5px] rounded-full px-1.5 py-px font-medium bg-gray-100 text-gray-600">archived</span>}
                </span>
              </td>
              <td className="py-1.5 pr-3 text-gray-500">{fmtAgo(f.last_activity ?? f.updated_at)}</td>
              <td className="py-1.5">
                <select value="" onChange={(e) => void reopen(f, e.target.value)} className="rounded-md border border-gray-300 bg-white px-1.5 py-0.5 text-xs text-gray-700">
                  <option value="">reopen…</option>
                  {!!f.archived && <option value="unarchive">unarchive</option>}
                  {f.phase === 'done' && <option value="build">phase → build</option>}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
