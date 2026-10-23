import { fileUrl } from '../api'

// F-29: the one place that renders a mock (docs pane, chat preview, full-view dialog). An html mock is code a role wrote,
// so it always runs in a sandboxed iframe WITHOUT `allow-same-origin`: it gets an opaque origin and cannot reach this page,
// its storage or the auth cookie. Do not add `allow-same-origin` here, or `allow-top-navigation`.
export const MOCK_SANDBOX = 'allow-scripts allow-forms allow-popups allow-modals'

export const THUMB_W = 240
export const THUMB_H = 150
const PAGE_W = 1280 // a thumbnail shows the page laid out at this size, scaled down
const PAGE_H = 800

export type MockKind = 'html' | 'image'

export default function MockView({ pid, fid, name, kind, mode }: { pid: string; fid: number; name: string; kind: MockKind; mode: 'thumb' | 'full' }) {
  const src = fileUrl(pid, fid, name)
  if (mode === 'thumb') {
    return (
      <div data-mock-thumb={name} className="relative overflow-hidden rounded-md border border-gray-200 bg-white" style={{ width: THUMB_W, height: THUMB_H }}>
        {kind === 'html' ? (
          // Live but inert: pointer-events off and out of the tab order, lazy so a long chat doesn't run every mock at once.
          <iframe
            title={`${name} (preview)`}
            src={src}
            sandbox={MOCK_SANDBOX}
            loading="lazy"
            tabIndex={-1}
            aria-hidden
            className="pointer-events-none absolute top-0 left-0 border-0 bg-white"
            style={{ width: PAGE_W, height: PAGE_H, transform: `scale(${THUMB_W / PAGE_W})`, transformOrigin: 'top left' }}
          />
        ) : (
          <img src={src} alt={name} loading="lazy" className="size-full object-cover" />
        )}
      </div>
    )
  }
  return kind === 'html' ? (
    <iframe title={name} src={src} sandbox={MOCK_SANDBOX} data-mock-frame={name} className="size-full min-h-0 border-0 bg-white" />
  ) : (
    <div className="flex size-full min-h-0 items-center justify-center overflow-auto bg-gray-100 p-4">
      <img src={src} alt={name} data-mock-image={name} className="max-h-full max-w-full object-contain" />
    </div>
  )
}
