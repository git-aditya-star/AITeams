import { useCallback, useEffect, useState } from 'react'
import type { FeatureDoc } from '../api'
import { api, fmtAgo } from '../api'
import { roleName } from '../roles'
import Markdown from './Markdown'
import { Button } from './ui/button'

export default function FeatureDocs({ pid, featureId, refreshKey }: { pid: string; featureId: number; refreshKey?: number }) {
  const [docs, setDocs] = useState<FeatureDoc[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [text, setText] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(() => {
    api
      .featureDocs(pid, featureId)
      .then((ds) => {
        setDocs(ds)
        setErr(null)
        setSelected((s) => (s && ds.some((d) => d.name === s) ? s : ds[0]?.name ?? null))
      })
      .catch((e) => setErr(e.message))
  }, [pid, featureId])

  useEffect(() => {
    setSelected(null)
    setText(null)
    load()
  }, [load, refreshKey])

  useEffect(() => {
    if (!selected) return setText(null)
    api
      .featureDoc(pid, featureId, selected)
      .then((d) => setText(d.text))
      .catch((e) => setText(`could not load: ${e.message}`))
  }, [pid, featureId, selected, refreshKey])

  return (
    <div className="flex-1 flex min-h-0">
      <div className="w-[220px] shrink-0 border-r border-gray-200 overflow-y-auto py-2">
        <div className="px-3 flex items-center justify-between mb-1">
          <span className="text-caption uppercase tracking-wide text-gray-500 font-semibold">Docs</span>
          <Button variant="link" size="inline" className="text-caption font-normal" onClick={load}>
            refresh
          </Button>
        </div>
        {err && <div className="px-3 text-caption text-red-600">{err}</div>}
        {!err && docs.length === 0 && <div className="px-3 text-caption text-gray-400">none yet</div>}
        {docs.map((d) => (
          <button
            key={d.name}
            onClick={() => setSelected(d.name)}
            className={`w-full cursor-pointer text-left px-3 py-1.5 text-meta ${selected === d.name ? 'bg-blue-100 text-blue-900 font-medium' : 'text-gray-700 hover:bg-gray-100'}`}
          >
            <span className="block truncate font-mono text-code">{d.name}</span>
            <span className="block text-caption text-gray-400">
              {d.editor ? roleName(d.editor) : 'unknown'}
              {d.updated_at ? `, ${fmtAgo(d.updated_at)}` : ''}
            </span>
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {selected ? text == null ? <span className="text-gray-400 text-caption">Loading…</span> : <Markdown text={text} /> : <span className="text-gray-400 text-caption">Select a doc.</span>}
      </div>
    </div>
  )
}
