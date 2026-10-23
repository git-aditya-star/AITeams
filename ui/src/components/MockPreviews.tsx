import { ExternalLink } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { FeatureDoc } from '../api'
import { api, fileUrl } from '../api'
import { mockRefs } from '../mockRefs'
import { useAppSelector } from '../store'
import { selectPid } from '../store/selectors'
import MockView, { THUMB_W, THUMB_H, type MockKind } from './MockView'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from './ui/dialog'

// Which mocks exist, from the feature's docs list (an iframe can't report a 404 to us). A hit is kept; a miss refetches, since
// the message that links a file is usually posted right after the role saved it. Concurrent lookups share one request.
const lists = new Map<string, FeatureDoc[]>()
const inflight = new Map<string, Promise<FeatureDoc[]>>()

async function findMock(pid: string, fid: number, name: string): Promise<MockKind | null> {
  const key = `${pid}:${fid}`
  const isMock = (d: FeatureDoc) => d.name === name && (d.kind === 'html' || d.kind === 'image')
  const cached = lists.get(key)?.find(isMock)
  if (cached) return cached.kind as MockKind
  let req = inflight.get(key)
  if (!req) {
    req = api
      .featureDocs(pid, fid)
      .then((l) => (lists.set(key, l), l))
      .catch(() => [] as FeatureDoc[])
      .finally(() => inflight.delete(key))
    inflight.set(key, req)
  }
  const hit = (await req).find(isMock)
  return hit ? (hit.kind as MockKind) : null
}

function Preview({ pid, fid, name }: { pid: string; fid: number; name: string }) {
  const [kind, setKind] = useState<MockKind | 'loading' | 'missing'>('loading')
  useEffect(() => {
    let live = true
    setKind('loading')
    void findMock(pid, fid, name).then((k) => live && setKind(k ?? 'missing'))
    return () => {
      live = false
    }
  }, [pid, fid, name])

  if (kind === 'missing')
    return (
      <div data-mock-missing={name} className="text-caption text-gray-500">
        mock not found: {name}
      </div>
    )
  if (kind === 'loading') return <div className="rounded-md border border-gray-200 bg-gray-50" style={{ width: THUMB_W, height: THUMB_H }} />
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button type="button" data-mock-card={name} className="cursor-pointer text-left" title="Open the full view">
          <MockView pid={pid} fid={fid} name={name} kind={kind} mode="thumb" />
          <span className="mt-0.5 block truncate font-mono text-caption text-gray-500" style={{ maxWidth: THUMB_W }}>
            {name}
          </span>
        </button>
      </DialogTrigger>
      {/* Radix would focus the first non-link control, which is the mock's iframe (it comes before the close button):
          keys typed there never reach this page, so Esc would do nothing. Focus the dialog itself instead. */}
      <DialogContent
        data-mock-dialog={name}
        onOpenAutoFocus={(e) => {
          e.preventDefault()
          ;(e.currentTarget as HTMLElement).focus()
        }}
        className="h-[calc(100vh-4rem)] max-w-[calc(100vw-4rem)] grid-rows-[auto_minmax(0,1fr)] gap-2 p-4 sm:max-w-[calc(100vw-4rem)]"
      >
        <DialogHeader className="flex-row items-center gap-3 pr-8">
          <DialogTitle className="truncate font-mono text-body">{name}</DialogTitle>
          <DialogDescription className="sr-only">Full view of {name}</DialogDescription>
          <Button asChild variant="outline" size="xs">
            <a href={fileUrl(pid, fid, name)} target="_blank" rel="noreferrer">
              <ExternalLink />
              Open in new tab
            </a>
          </Button>
        </DialogHeader>
        <div className="min-h-0 overflow-hidden rounded-md border border-gray-200">
          <MockView pid={pid} fid={fid} name={name} kind={kind} mode="full" />
        </div>
      </DialogContent>
    </Dialog>
  )
}

export default function MockPreviews({ text }: { text: string }) {
  const pid = useAppSelector(selectPid)
  const refs = useMemo(() => mockRefs(text), [text])
  if (!pid || refs.length === 0) return null
  return (
    <div data-mock-previews className="mt-1.5 flex flex-wrap gap-3">
      {refs.map((r) => (
        <Preview key={`${r.fid}/${r.name}`} pid={pid} fid={r.fid} name={r.name} />
      ))}
    </div>
  )
}
