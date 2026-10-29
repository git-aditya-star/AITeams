import { AppWindow, ExternalLink, FileText, Image, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { DocKind, FeatureDoc } from '../api'
import { api, fileUrl } from '../api'
import { claudeHref, sourceName } from '../artifacts'
import Markdown from './Markdown'
import MockView from './MockView'
import { Badge } from './ui/badge'
import { Button } from './ui/button'

// F-29: a feature's docs are its .md files, its mocks (html pages, images) and its published Claude artifacts.
export const KIND_ICON = { md: FileText, html: AppWindow, image: Image, artifact: Sparkles } as const
export const kindOf = (d: FeatureDoc | undefined): DocKind => d?.kind ?? 'md'
// An artifact's name is its title, which could equal a file's name: key it apart.
export const keyOf = (d: FeatureDoc) => (kindOf(d) === 'artifact' ? `artifact:${d.url ?? d.name}` : d.name)

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
          <MockView key={`${file}:${source.updated_at}`} src={fileUrl(pid, featureId, file)} name={file} kind={source.kind} mode="full" />
        </div>
      ) : (
        <div data-artifact-no-source className="px-4 py-2 text-caption text-gray-400">
          {file ? `source file not found: ${file}` : 'No local source recorded.'}
        </div>
      )}
    </div>
  )
}


// F-40: the one place that renders a feature doc (markdown text, an html/image mock framed, a published artifact), for the Docs
// tab and for the side panel an approval card opens. `docs` is the feature's whole list (an artifact finds its local source in it).
export default function DocPane({ pid, featureId, doc, docs, refreshKey }: { pid: string; featureId: number; doc: FeatureDoc | undefined; docs: FeatureDoc[]; refreshKey?: number }) {
  const [text, setText] = useState<string | null>(null)
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
    <>
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
        <MockView key={`${doc.name}:${doc.updated_at}`} src={fileUrl(pid, featureId, doc.name)} name={doc.name} kind={kind as 'html' | 'image'} mode="full" />
      </div>
    </div>
  ) : (
    <div className="flex-1 overflow-y-auto px-6 py-4">
      {doc ? text == null ? <span className="text-gray-400 text-caption">Loading…</span> : <Markdown text={text} /> : <span className="text-gray-400 text-caption">Select a doc.</span>}
    </div>
  )}
    </>
  )
}
