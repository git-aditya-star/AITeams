import { X } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FeatureDoc } from '../api'
import { api, fmtAgo } from '../api'
import { roleName } from '../roles'
import { useAppDispatch, useAppSelector } from '../store'
import { selectDocPanel, selectFeatures } from '../store/selectors'
import { docPanel } from '../store/slices'
import DocPane from './DocPane'
import { Button } from './ui/button'

// F-40: the doc an approval card opened ("Open design.md"), in a panel beside the Inbox, chat or History, so the Master reads the
// doc and answers the card without leaving either. It renders with the same pane as the Docs tab. Opening another doc replaces
// it; changing view keeps it; the close button (or opening nothing) clears it.
export default function DocPanel({ pid, refreshKey }: { pid: string; refreshKey?: number }) {
  const dispatch = useAppDispatch()
  const open = useAppSelector(selectDocPanel)
  const features = useAppSelector(selectFeatures)
  const [docs, setDocs] = useState<FeatureDoc[]>([])
  const [err, setErr] = useState<string | null>(null)
  const featureId = open?.featureId
  const name = open?.name

  useEffect(() => {
    if (featureId == null) return
    let live = true
    api
      .featureDocs(pid, featureId)
      .then((ds) => live && (setDocs(ds), setErr(null)))
      .catch((e) => live && (setDocs([]), setErr(e.message)))
    return () => {
      live = false
    }
  }, [pid, featureId, name, refreshKey])

  if (!open) return null
  const feature = features.find((f) => f.id === open.featureId)
  const doc = docs.find((d) => d.name === open.name && d.kind !== 'artifact')
  return (
    <aside data-doc-panel={open.name} aria-label={`${open.name} (side panel)`} className="flex h-full w-[440px] shrink-0 flex-col border-l border-gray-200 bg-white">
      <div className="flex shrink-0 items-center gap-2 border-b border-gray-200 px-4 py-2">
        {feature && <span className="font-mono text-caption text-gray-500">{feature.key}</span>}
        <span data-doc-panel-name className="truncate font-mono text-code font-medium text-gray-800">
          {open.name}
        </span>
        {doc?.updated_at ? (
          <span className="ml-auto shrink-0 text-caption text-gray-400">
            {doc.editor ? `${roleName(doc.editor)} · ` : ''}
            {fmtAgo(doc.updated_at)}
          </span>
        ) : (
          <span className="ml-auto" />
        )}
        <Button variant="ghost" size="icon-xs" aria-label="Close doc panel" className="text-gray-500" onClick={() => dispatch(docPanel.actions.closed())}>
          <X className="size-4" />
        </Button>
      </div>
      {err ? (
        <div className="px-4 py-3 text-caption text-red-600">{err}</div>
      ) : docs.length > 0 && !doc ? (
        <div data-doc-panel-missing className="px-4 py-3 text-caption text-gray-500">
          {open.name} is not in this feature's docs folder.
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          <DocPane pid={pid} featureId={open.featureId} doc={doc} docs={docs} refreshKey={refreshKey} />
        </div>
      )}
    </aside>
  )
}
