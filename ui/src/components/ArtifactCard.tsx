import { ExternalLink, Sparkles } from 'lucide-react'
import type { Artifact } from '../api'
import { claudeHref } from '../artifacts'
import { Badge } from './ui/badge'
import { Button } from './ui/button'

// F-29 Part B: the link card for a page a role published to claude.ai (ref `artifact:<id>`). It never embeds the page: Open goes
// to claude.ai in a new tab, where it is private to the Master's account. The local source is shown as text (Docs previews it).
export default function ArtifactCard({ a }: { a: Artifact }) {
  const href = claudeHref(a.url)
  return (
    <div data-artifact-card={a.id} className="mt-1.5 flex max-w-md items-center gap-3 rounded-md border border-gray-200 bg-white px-3 py-2">
      <Sparkles className="size-4 shrink-0 text-amber-600" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span data-artifact-title className="truncate text-body font-medium">
            {a.title}
          </span>
          {a.version > 1 && (
            <Badge data-artifact-version className="bg-blue-50 text-blue-700">
              v{a.version} · updated
            </Badge>
          )}
        </div>
        <div data-artifact-source className="truncate font-mono text-caption text-gray-500" title={a.path}>
          {a.path}
        </div>
      </div>
      {href && (
        <Button asChild variant="outline" size="sm">
          <a href={href} target="_blank" rel="noreferrer">
            <ExternalLink />
            Open
          </a>
        </Button>
      )}
    </div>
  )
}
