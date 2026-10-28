import { useCallback, useEffect, useState } from 'react'
import type { FeatureDoc } from '../api'
import { api, fmtAgo } from '../api'
import { roleName } from '../roles'
import DocPane, { KIND_ICON, keyOf, kindOf } from './DocPane'
import { Button } from './ui/button'

export default function FeatureDocs({ pid, featureId, refreshKey }: { pid: string; featureId: number; refreshKey?: number }) {
  const [docs, setDocs] = useState<FeatureDoc[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(() => {
    api
      .featureDocs(pid, featureId)
      .then((ds) => {
        setDocs(ds)
        setErr(null)
        setSelected((s) => (s && ds.some((d) => keyOf(d) === s) ? s : ds[0] ? keyOf(ds[0]) : null))
      })
      .catch((e) => setErr(e.message))
  }, [pid, featureId])

  useEffect(() => {
    setSelected(null)
    load()
  }, [load, refreshKey])

  const doc = docs.find((d) => keyOf(d) === selected)

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
        {docs.map((d) => {
          const Icon = KIND_ICON[kindOf(d)]
          return (
            <button
              key={keyOf(d)}
              onClick={() => setSelected(keyOf(d))}
              className={`w-full cursor-pointer text-left px-3 py-1.5 text-meta ${selected === keyOf(d) ? 'bg-blue-100 text-blue-900 font-medium' : 'text-gray-700 hover:bg-gray-100'}`}
            >
              <span className="flex items-center gap-1.5">
                <Icon className="size-3.5 shrink-0 text-gray-400" />
                <span data-doc-kind={kindOf(d)} className={`block truncate text-code ${kindOf(d) === 'artifact' ? '' : 'font-mono'}`}>{d.name}</span>
              </span>
              <span className="block text-caption text-gray-400">
                {d.editor ? roleName(d.editor) : 'unknown'}
                {d.updated_at ? `, ${fmtAgo(d.updated_at)}` : ''}
              </span>
            </button>
          )
        })}
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <DocPane pid={pid} featureId={featureId} doc={doc} docs={docs} refreshKey={refreshKey} />
      </div>
    </div>
  )
}
