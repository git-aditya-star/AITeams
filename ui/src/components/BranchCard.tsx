import { TriangleAlert } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import type { Approval, BranchChoice, BranchPayload } from '../api'
import { api } from '../api'
import { useAppSelector } from '../store'
import { selectPid } from '../store/selectors'
import { Alert } from './ui/alert'
import { Input } from './ui/input'
import { RadioGroup, RadioGroupItem } from './ui/radio-group'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'

// F-39: the body of a `Branch:` approval. The Lead proposes where a feature's code goes (a new branch in its own worktree, the
// current branch, or another feature's worktree); the Master edits it here and Approve sends the edited choice. Warnings and
// errors for the current choice come live from the daemon (`/branches/check`), so editing the name into `release/1.2` shows the
// shared-branch warning and an unknown base or a taken name blocks Approve.

// What ApprovalCard needs back: the choice to send with Approve, and whether Approve must wait.
export type BranchEdit = { choice: BranchChoice; blocked: boolean }

export const isBranchTitle = (title: string) => /^Branch:/i.test(title)

// The payload arrives decoded; a daemon that stores it as text sends the JSON string.
export function branchPayload(a: Approval): BranchPayload | null {
  let p: unknown = a.payload
  if (typeof p === 'string') {
    try {
      p = JSON.parse(p)
    } catch {
      return null
    }
  }
  return p && typeof p === 'object' && 'mode' in p ? (p as BranchPayload) : null
}

const tildify = (path: string) => path.replace(/^\/(?:Users|home)\/[^/]+/, '~')

const MODE_TITLE = { new: 'New branch in its own worktree', current: 'Current branch', shared: 'Existing worktree' } as const

function Where({ branch, tree }: { branch: string; tree: string }) {
  return (
    <>
      Commits land on <b className="font-mono">{branch || '…'}</b> in <span className="font-mono text-caption">{tree}</span>
    </>
  )
}

export default function BranchCard({ p, pending, onEdit }: { p: BranchPayload; pending: boolean; onEdit: (e: BranchEdit) => void }) {
  const pid = useAppSelector(selectPid)
  const uid = useId()
  const worktrees = p.worktrees ?? []
  const [mode, setMode] = useState(p.mode)
  const [name, setName] = useState(p.mode === 'new' ? (p.branch ?? '') : '')
  const [base, setBase] = useState(p.base ?? '')
  const [shared, setShared] = useState(String(p.shared_from ?? worktrees[0]?.feature ?? ''))
  const [bases, setBases] = useState<string[]>([])
  const [check, setCheck] = useState<{ warnings: string[]; errors: string[] } | null>(null)
  const chosen = worktrees.find((w) => w.feature === shared)

  // The bases a new branch can start from (local and remote branches, newest first); the Lead's own pick is always an option.
  useEffect(() => {
    if (!pid || !pending) return
    api
      .branches(pid)
      .then((r) => setBases(r.branches.map((b) => b.name)))
      .catch(() => {})
  }, [pid, pending])

  const choice: BranchChoice = mode === 'new' ? { mode, branch: name.trim(), base } : mode === 'current' ? { mode } : { mode, shared_from: shared }

  // Live warnings and errors, a beat after the last edit. A reply that arrives after a newer edit is dropped.
  useEffect(() => {
    if (!pid || !pending) return
    let live = true
    const t = setTimeout(() => {
      api
        .checkBranch(pid, { mode, branch: mode === 'new' ? name.trim() : mode === 'current' ? (p.current ?? '') : (chosen?.branch ?? ''), base: mode === 'new' ? base : '', shared_from: mode === 'shared' ? shared : '' })
        .then((r) => live && setCheck({ warnings: r.warnings ?? [], errors: r.errors ?? [] }))
        .catch(() => live && setCheck(null))
    }, 250)
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [pid, pending, mode, name, base, shared, p.current, chosen?.branch])

  const warnings = check ? check.warnings : (p.warnings ?? [])
  const errors = check ? check.errors : []
  const blocked = errors.length > 0 || (mode === 'new' && !name.trim()) || (mode === 'shared' && !chosen)
  const key = JSON.stringify(choice)
  useEffect(() => onEdit({ choice: JSON.parse(key), blocked }), [key, blocked, onEdit])

  const land =
    mode === 'new' ? { branch: name.trim(), tree: p.tree ? tildify(p.tree) : 'its own worktree' } : mode === 'current' ? { branch: p.current ?? '', tree: 'the main folder' } : { branch: chosen?.branch ?? '', tree: chosen ? tildify(chosen.tree) : '' }

  if (!pending) {
    // Decided: what was approved, from the stored choice.
    const sel = p.mode === 'shared' ? worktrees.find((w) => w.feature === String(p.shared_from)) : undefined
    const done = p.mode === 'new' ? { branch: p.branch ?? '', tree: p.tree ? tildify(p.tree) : 'its own worktree' } : p.mode === 'current' ? { branch: p.branch ?? p.current ?? '', tree: 'the main folder' } : { branch: sel?.branch ?? p.branch ?? '', tree: sel ? tildify(sel.tree) : '' }
    return (
      <div data-branch-card data-branch-mode={p.mode} className="mt-1.5 text-gray-700">
        <span className="text-gray-500">{MODE_TITLE[p.mode]}{p.mode === 'new' && p.base ? ` from ${p.base}` : ''}: </span>
        <span data-lands-on>
          <Where {...done} />
        </span>
      </div>
    )
  }

  const row = (m: typeof mode, extra?: React.ReactNode, hint?: React.ReactNode, disabled = false) => (
    <div className={`rounded-md border px-3 py-2 ${mode === m ? 'border-gray-900' : 'border-gray-200'} ${disabled ? 'opacity-60' : ''}`}>
      <div className="flex items-center gap-2">
        <RadioGroupItem value={m} id={`${uid}-${m}`} disabled={disabled} />
        <label htmlFor={`${uid}-${m}`} className={`font-medium ${disabled ? '' : 'cursor-pointer'}`}>
          {MODE_TITLE[m]}
        </label>
      </div>
      {hint && <div className="mt-0.5 pl-6 text-caption text-gray-500">{hint}</div>}
      {mode === m && extra && <div className="mt-2 pl-6">{extra}</div>}
    </div>
  )

  return (
    <div data-branch-card data-branch-mode={mode} className="mt-2 flex flex-col gap-2">
      <RadioGroup value={mode} onValueChange={(v) => setMode(v as typeof mode)} aria-label="Where the code goes">
        {row(
          'new',
          <div className="grid grid-cols-[44px_1fr] items-center gap-2">
            <span className="text-caption text-gray-500">Name</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} aria-label="Branch name" className="font-mono" spellCheck={false} />
            <span className="text-caption text-gray-500">Base</span>
            <Select value={base} onValueChange={setBase}>
              <SelectTrigger aria-label="Base" className="w-full font-mono">
                <SelectValue placeholder="choose a base" />
              </SelectTrigger>
              <SelectContent>
                {[...new Set([...(base ? [base] : []), ...bases])].map((b) => (
                  <SelectItem key={b} value={b} className="font-mono">
                    {b}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>,
        )}
        {row(
          'current',
          undefined,
          <>
            The main folder, on <span className="font-mono">{p.current || '?'}</span>
          </>,
        )}
        {row(
          'shared',
          <Select value={shared} onValueChange={setShared}>
            <SelectTrigger aria-label="Worktree" className="w-full font-mono">
              <SelectValue placeholder="choose a worktree" />
            </SelectTrigger>
            <SelectContent>
              {worktrees.map((w) => (
                <SelectItem key={w.feature} value={w.feature} className="font-mono">
                  {w.feature} · {w.branch}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>,
          worktrees.length ? (
            <>
              {worktrees.map((w) => w.feature).join(', ')} {worktrees.length === 1 ? 'has' : 'have'} a worktree
            </>
          ) : (
            'No other feature has a worktree yet'
          ),
          worktrees.length === 0,
        )}
      </RadioGroup>
      {warnings.length > 0 && (
        <Alert variant="warning" data-branch-warning>
          <TriangleAlert />
          <div className="col-start-2">
            {warnings.map((w) => (
              <div key={w}>{w}</div>
            ))}
          </div>
        </Alert>
      )}
      {errors.length > 0 && (
        <Alert variant="destructive" data-branch-error>
          <div className="col-start-2">
            {errors.map((e) => (
              <div key={e}>{e}</div>
            ))}
          </div>
        </Alert>
      )}
      <div data-lands-on className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-gray-700">
        <Where {...land} />
      </div>
    </div>
  )
}
