import { ArrowRight, ExternalLink, GitBranch } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { Approval, PRChoice, PRCommit, PRPayload, PRPreview } from '../api'
import { api } from '../api'
import { useAppSelector } from '../store'
import { selectPid } from '../store/selectors'
import { Alert } from './ui/alert'
import { Input } from './ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { Textarea } from './ui/textarea'

// F-39: the body of a `PR:` approval. The feature's Lead asks the Master to open a pull request for the feature's branch; the Master
// edits the destination, title and description here, and Approve sends the edited three. On approve the daemon pushes the branch to
// origin and runs `gh pr create`; a failure (no origin, no gh, not logged in) is a 409 that ApprovalCard shows under the buttons while
// this card stays open. Which commits the PR carries depends on the destination, so changing it asks the daemon again
// (`/branches/pr-preview`); an `errors` answer ("nothing to open a pull request for") blocks Approve.

// What ApprovalCard needs back: the choice to send with Approve, and whether Approve must wait.
export type PREdit = { choice: PRChoice; blocked: boolean }

export const isPRTitle = (title: string) => /^PR:/i.test(title)

// The payload arrives decoded; a daemon that stores it as text sends the JSON string.
export function prPayload(a: Approval): PRPayload | null {
  let p: unknown = a.payload
  if (typeof p === 'string') {
    try {
      p = JSON.parse(p)
    } catch {
      return null
    }
  }
  return p && typeof p === 'object' && 'source' in p && 'destination' in p ? (p as PRPayload) : null
}

// Only a web link is ever rendered as one (the URL is what `gh` printed).
export const webUrl = (url: string | null | undefined) => (url && /^https?:\/\//i.test(url) ? url : null)
export const prNumber = (url: string) => /\/pull\/(\d+)/.exec(url)?.[1] ?? null

function Commits({ commits }: { commits: PRCommit[] }) {
  return (
    <div data-pr-commits>
      <div className="mb-1 text-caption font-medium uppercase tracking-wide text-gray-500">
        {commits.length} commit{commits.length === 1 ? '' : 's'}
      </div>
      <ul className="max-h-32 space-y-0.5 overflow-y-auto rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5">
        {commits.map((c) => (
          <li key={c.sha} className="flex gap-2">
            <span className="shrink-0 font-mono text-caption text-gray-500">{c.sha}</span>
            <span className="min-w-0 truncate">{c.subject}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function PRCard({ a, p, pending, onEdit }: { a: Approval; p: PRPayload; pending: boolean; onEdit: (e: PREdit) => void }) {
  const pid = useAppSelector(selectPid)
  const [dest, setDest] = useState(p.destination)
  const [title, setTitle] = useState(p.title)
  const [body, setBody] = useState(p.body ?? '')
  const [preview, setPreview] = useState<PRPreview | null>(null)
  const featureId = a.feature_id

  // The commits and the branches to pick from, fresh when the card opens and again for each destination. A reply that arrives after
  // a newer pick is dropped; a failed lookup leaves the stored commits (the daemon judges the request again on Approve).
  useEffect(() => {
    if (!pid || !pending || featureId == null) return
    let live = true
    api
      .prPreview(pid, featureId, dest)
      .then((r) => live && setPreview(r))
      .catch(() => live && setPreview(null))
    return () => {
      live = false
    }
  }, [pid, pending, featureId, dest])

  const commits = preview?.commits ?? p.commits ?? []
  const errors = preview?.errors ?? []
  const blocked = errors.length > 0 || !title.trim() || !dest
  const key = JSON.stringify({ destination: dest, title: title.trim(), body })
  useEffect(() => onEdit({ choice: JSON.parse(key), blocked }), [key, blocked, onEdit])

  if (!pending) {
    // Decided: what was opened (or asked for), from the stored payload; `pr_url` is the pull request once the daemon opened it.
    const url = webUrl(p.pr_url)
    return (
      <div data-pr-card data-pr-decided className="mt-1.5 text-gray-700">
        <span className="font-mono">{p.source}</span> <ArrowRight className="inline size-3.5 text-gray-400" /> <span className="font-mono">{p.destination}</span>
        <span className="text-gray-500"> · {p.title}</span>
        {url && (
          <a data-pr-link href={url} target="_blank" rel="noreferrer noopener" className="ml-2 inline-flex items-center gap-1 text-blue-700 hover:underline">
            Pull request{prNumber(url) ? ` #${prNumber(url)}` : ''}
            <ExternalLink className="size-3" />
          </a>
        )}
      </div>
    )
  }

  // The branches to pick from: what the daemon listed, and always the Lead's own pick and the current one.
  const options = [...new Set([p.destination, dest, ...(preview?.branches ?? []).map((b) => b.name)])].filter(Boolean)

  return (
    <div data-pr-card className="mt-2 flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span data-pr-source className="inline-flex items-center gap-1 font-mono">
          <GitBranch className="size-3.5 text-gray-500" />
          {p.source}
        </span>
        <ArrowRight className="size-3.5 text-gray-400" />
        <Select value={dest} onValueChange={setDest}>
          <SelectTrigger aria-label="Destination" className="w-56 font-mono">
            <SelectValue placeholder="choose a branch" />
          </SelectTrigger>
          <SelectContent>
            {options.map((b) => (
              <SelectItem key={b} value={b} className="font-mono">
                {b}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Pull request title" placeholder="Title" />
      <Textarea value={body} onChange={(e) => setBody(e.target.value)} aria-label="Pull request description" placeholder="Description" rows={5} className="resize-y" />
      {commits.length > 0 && <Commits commits={commits} />}
      {errors.length > 0 && (
        <Alert variant="destructive" data-pr-error>
          <div className="col-start-2">
            {errors.map((e) => (
              <div key={e}>{e}</div>
            ))}
          </div>
        </Alert>
      )}
      <div data-pr-note className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-gray-700">
        AITeams pushes <b className="font-mono">{p.source}</b> to origin and opens the pull request on GitHub. You merge it there.
      </div>
    </div>
  )
}
