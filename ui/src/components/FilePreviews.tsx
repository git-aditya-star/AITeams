import { ExternalLink } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { distinctRefs, fileRefs, refName } from '../fileRefs'
import { useAppSelector } from '../store'
import { selectPid } from '../store/selectors'
import MockView, { THUMB_W, THUMB_H, type MockKind } from './MockView'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from './ui/dialog'

// F-50: the files a surface's texts name (a message, an approval, a question or a commit card), as thumbnails at its end. One resolve
// call per surface tells which exist, since an iframe can't report a 404 to us. A hit is kept; a miss is asked again, since the
// message that names a file is usually posted right after the role saved it. Concurrent lookups of one ref share a request.
const MAX_THUMBS = 4
const MAX_REFS = 20 // what the daemon resolves in one call
const isBare = (ref: string) => !ref.includes('/')

interface Hit {
  kind: MockKind
  url: string
}
const hits = new Map<string, Hit>()
const inflight = new Map<string, Promise<Hit | null>>()

async function resolveRefs(pid: string, featureId: number | null, refs: string[]): Promise<Map<string, Hit | null>> {
  const key = (ref: string) => `${pid}:${featureId ?? ''}:${ref}`
  const out = new Map<string, Hit | null>()
  const asking: Promise<void>[] = []
  const fresh: string[] = []
  for (const ref of refs) {
    const hit = hits.get(key(ref))
    if (hit) out.set(ref, hit)
    else if (inflight.has(key(ref))) asking.push(inflight.get(key(ref))!.then((h) => void out.set(ref, h)))
    else fresh.push(ref)
  }
  if (fresh.length) {
    const call = api.resolvePreviews(pid, featureId, fresh).catch(() => [])
    for (const ref of fresh) {
      const one = call
        .then((list) => {
          const r = list.find((x) => x.ref === ref)
          const hit = r?.kind && r.url ? { kind: r.kind, url: r.url } : null
          if (hit) hits.set(key(ref), hit)
          return hit
        })
        .finally(() => inflight.delete(key(ref)))
      inflight.set(key(ref), one)
      asking.push(one.then((h) => void out.set(ref, h)))
    }
  }
  await Promise.all(asking)
  return out
}

function Thumb({ name, hit }: { name: string; hit: Hit }) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button type="button" data-mock-card={name} className="cursor-pointer text-left" title="Open the full view">
          <MockView src={hit.url} name={name} kind={hit.kind} mode="thumb" />
          <span className="mt-0.5 block truncate font-mono text-caption text-gray-500" style={{ maxWidth: THUMB_W }}>
            {name}
          </span>
        </button>
      </DialogTrigger>
      {/* Radix would focus the first non-link control, which is the file's iframe (it comes before the close button):
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
            <a href={hit.url} target="_blank" rel="noreferrer">
              <ExternalLink />
              Open in new tab
            </a>
          </Button>
        </DialogHeader>
        <div className="min-h-0 overflow-hidden rounded-md border border-gray-200">
          <MockView src={hit.url} name={name} kind={hit.kind} mode="full" />
        </div>
      </DialogContent>
    </Dialog>
  )
}

// `featureId` is the context feature: bare names (and relative paths, in its tree first) resolve there. null = no feature.
export default function FilePreviews({ texts, featureId }: { texts: (string | null | undefined)[]; featureId: number | null }) {
  const pid = useAppSelector(selectPid)
  const joined = texts.filter(Boolean).join('\n')
  // With no context feature a bare name has nowhere to resolve: it is an ordinary word there, not a missing file.
  const refs = useMemo(() => distinctRefs(fileRefs(joined).filter((r) => featureId != null || r.includes('/')), featureId), [joined, featureId])
  const [found, setFound] = useState<Map<string, Hit | null> | null>(null)
  useEffect(() => {
    if (!pid || refs.length === 0) return
    let live = true
    setFound(null)
    void resolveRefs(pid, featureId, refs.slice(0, MAX_REFS)).then((m) => live && setFound(m))
    return () => {
      live = false
    }
  }, [pid, featureId, refs])
  if (!pid || refs.length === 0) return null

  // A bare name is mostly a word in prose ("index.html"): it shows only when it resolves, and takes a place only then (no chip, no
  // placeholder while loading, so nothing jumps). A ref with a "/" always takes a place, as a thumbnail or the "not found" chip.
  const shown = found ? refs.filter((ref) => found.has(ref) && (found.get(ref) || !isBare(ref))) : refs.filter((ref) => !isBare(ref))
  // the extra refs (past the fourth place) show as links, and only the ones that exist
  const extras = shown.slice(MAX_THUMBS).flatMap((ref) => {
    const hit = found?.get(ref)
    return hit ? [{ ref, url: hit.url }] : []
  })
  return (
    <>
      {shown.length > 0 && (
        <div data-mock-previews className="mt-1.5 flex flex-wrap gap-3">
          {shown.slice(0, MAX_THUMBS).map((ref) => {
            const name = refName(ref)
            if (!found) return <div key={ref} className="rounded-md border border-gray-200 bg-gray-50" style={{ width: THUMB_W, height: THUMB_H }} />
            const hit = found.get(ref)
            return hit ? (
              <Thumb key={ref} name={name} hit={hit} />
            ) : (
              <div key={ref} data-mock-missing={name} title={ref} className="text-caption text-gray-500">
                not found: {name}
              </div>
            )
          })}
        </div>
      )}
      {extras.length > 0 && (
        <div data-file-extras className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-caption">
          <span className="text-gray-500">More:</span>
          {extras.map((x) => (
            <Button key={x.ref} asChild variant="link" size="inline" className="text-caption">
              <a href={x.url} target="_blank" rel="noreferrer" data-file-extra={refName(x.ref)} title={x.ref}>
                {refName(x.ref)}
                <ExternalLink className="size-3" />
              </a>
            </Button>
          ))}
        </div>
      )}
    </>
  )
}
