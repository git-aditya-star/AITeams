import { AppWindow, ExternalLink, FileText, Image } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import type { DocKind, FeatureDoc } from '../api'
import { api, fileUrl, fmtAgo } from '../api'
import { roleName } from '../roles'
import Markdown from './Markdown'
import MockView from './MockView'
import { Button } from './ui/button'

// F-29: a feature's docs are its .md files and its mocks (html pages, images).
const KIND_ICON = { md: FileText, html: AppWindow, image: Image } as const
const kindOf = (d: FeatureDoc | undefined): DocKind => d?.kind ?? 'md'

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

  const doc = docs.find((d) => d.name === selected)
  const kind = kindOf(doc)
  useEffect(() => {
    if (!selected || kind !== 'md') return setText(null)
    api
      .featureDoc(pid, featureId, selected)
      .then((d) => setText(d.text))
      .catch((e) => setText(`could not load: ${e.message}`))
  }, [pid, featureId, selected, kind, refreshKey])

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
              key={d.name}
              onClick={() => setSelected(d.name)}
              className={`w-full cursor-pointer text-left px-3 py-1.5 text-meta ${selected === d.name ? 'bg-blue-100 text-blue-900 font-medium' : 'text-gray-700 hover:bg-gray-100'}`}
            >
              <span className="flex items-center gap-1.5">
                <Icon className="size-3.5 shrink-0 text-gray-400" />
                <span data-doc-kind={kindOf(d)} className="block truncate font-mono text-code">{d.name}</span>
              </span>
              <span className="block text-caption text-gray-400">
                {d.editor ? roleName(d.editor) : 'unknown'}
                {d.updated_at ? `, ${fmtAgo(d.updated_at)}` : ''}
              </span>
            </button>
          )
        })}
      </div>
      {selected && kind !== 'md' ? (
        <div className="flex min-h-0 flex-1 flex-col" data-doc-mock={selected}>
          <div className="flex shrink-0 items-center gap-2 border-b border-gray-200 px-4 py-1.5">
            <span className="truncate font-mono text-code text-gray-700">{selected}</span>
            <Button asChild variant="outline" size="xs" className="ml-auto">
              <a href={fileUrl(pid, featureId, selected)} target="_blank" rel="noreferrer">
                <ExternalLink />
                Open in new tab
              </a>
            </Button>
          </div>
          {/* the key remounts the frame when the file changes, so a mock edited by a role shows its new version */}
          <div className="min-h-0 flex-1">
            <MockView key={`${selected}:${doc?.updated_at}`} pid={pid} fid={featureId} name={selected} kind={kind as 'html' | 'image'} mode="full" />
          </div>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {selected ? text == null ? <span className="text-gray-400 text-caption">Loading…</span> : <Markdown text={text} /> : <span className="text-gray-400 text-caption">Select a doc.</span>}
        </div>
      )}
    </div>
  )
}
