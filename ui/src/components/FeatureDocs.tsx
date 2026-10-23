import { AppWindow, ExternalLink, FileText, Image, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import type { DocKind, FeatureDoc } from '../api'
import { api, fileUrl, fmtAgo } from '../api'
import { claudeHref, sourceName } from '../artifacts'
import { roleName } from '../roles'
import Markdown from './Markdown'
import MockView from './MockView'
import { Badge } from './ui/badge'
import { Button } from './ui/button'

// F-29: a feature's docs are its .md files, its mocks (html pages, images) and its published Claude artifacts.
const KIND_ICON = { md: FileText, html: AppWindow, image: Image, artifact: Sparkles } as const
const kindOf = (d: FeatureDoc | undefined): DocKind => d?.kind ?? 'md'
// An artifact's name is its title, which could equal a file's name: key it apart.
const keyOf = (d: FeatureDoc) => (kindOf(d) === 'artifact' ? `artifact:${d.url ?? d.name}` : d.name)

// An artifact's pane: the claude.ai page is only linked (private to the Master's account); the local source it was published
// from is previewed here when its file is still in the docs folder.
function ArtifactPane({ pid, featureId, doc, source }: { pid: string; featureId: number; doc: FeatureDoc; source: FeatureDoc | undefined }) {
  const href = claudeHref(doc.url)
  const file = doc.path ? sourceName(doc.path) : null
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-doc-artifact={doc.name}>
      <div className="flex shrink-0 items-center gap-2 border-b border-gray-200 px-4 py-1.5">
        <span data-artifact-title className="truncate text-body font-medium text-gray-800">
          {doc.name}
        </span>
        {(doc.version ?? 1) > 1 && (
          <Badge data-artifact-version className="bg-blue-50 text-blue-700">
            v{doc.version} · updated
          </Badge>
        )}
        {href && (
          <Button asChild variant="outline" size="xs" className="ml-auto">
            <a href={href} target="_blank" rel="noreferrer">
              <ExternalLink />
              Open
            </a>
          </Button>
        )}
      </div>
      <div className="shrink-0 truncate px-4 py-1 text-caption text-gray-500">
        Local source: <span data-artifact-source className="font-mono">{doc.path ?? 'unknown'}</span>
      </div>
      {file && source && (source.kind === 'html' || source.kind === 'image') ? (
        // the key remounts the frame when a role edits the file
        <div className="min-h-0 flex-1 border-t border-gray-200">
          <MockView key={`${file}:${source.updated_at}`} pid={pid} fid={featureId} name={file} kind={source.kind} mode="full" />
        </div>
      ) : (
        <div data-artifact-no-source className="px-4 py-2 text-caption text-gray-400">
          {file ? `source file not found: ${file}` : 'No local source recorded.'}
        </div>
      )}
    </div>
  )
}

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
        setSelected((s) => (s && ds.some((d) => keyOf(d) === s) ? s : ds[0] ? keyOf(ds[0]) : null))
      })
      .catch((e) => setErr(e.message))
  }, [pid, featureId])

  useEffect(() => {
    setSelected(null)
    setText(null)
    load()
  }, [load, refreshKey])

  const doc = docs.find((d) => keyOf(d) === selected)
  const kind = kindOf(doc)
  const name = doc?.name ?? null
  useEffect(() => {
    if (!name || kind !== 'md') return setText(null)
    api
      .featureDoc(pid, featureId, name)
      .then((d) => setText(d.text))
      .catch((e) => setText(`could not load: ${e.message}`))
  }, [pid, featureId, name, kind, refreshKey])

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
      {doc && kind === 'artifact' ? (
        <ArtifactPane pid={pid} featureId={featureId} doc={doc} source={docs.find((d) => d.name === (doc.path ? sourceName(doc.path) : null) && d.kind !== 'artifact')} />
      ) : doc && kind !== 'md' ? (
        <div className="flex min-h-0 flex-1 flex-col" data-doc-mock={doc.name}>
          <div className="flex shrink-0 items-center gap-2 border-b border-gray-200 px-4 py-1.5">
            <span className="truncate font-mono text-code text-gray-700">{doc.name}</span>
            <Button asChild variant="outline" size="xs" className="ml-auto">
              <a href={fileUrl(pid, featureId, doc.name)} target="_blank" rel="noreferrer">
                <ExternalLink />
                Open in new tab
              </a>
            </Button>
          </div>
          {/* the key remounts the frame when the file changes, so a mock edited by a role shows its new version */}
          <div className="min-h-0 flex-1">
            <MockView key={`${doc.name}:${doc.updated_at}`} pid={pid} fid={featureId} name={doc.name} kind={kind as 'html' | 'image'} mode="full" />
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
