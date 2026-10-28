import { GitBranch } from 'lucide-react'
import type { FeatureBranch } from '../api'
import { Badge } from './ui/badge'
import { Tip } from './ui/tooltip'

// F-39: where a feature's code goes, in the feature header. Three forms: the branch (new, or another feature's worktree) with its
// tree and base in the tooltip, "current folder (<branch>)", and an amber "branch not confirmed" for a feature that must get a
// `Branch:` approval before any commit request and has none yet. A feature that predates the gate shows nothing.
const tildify = (path: string) => path.replace(/^\/(?:Users|home)\/[^/]+/, '~')

export default function BranchChip({ branch, scrollable }: { branch: FeatureBranch | undefined; scrollable: boolean }) {
  if (!branch) return null
  const { state, mode, gated } = branch
  const id = branch.approval_id
  if (state !== 'approved') {
    if (state === 'none' && !gated) return null
    const why =
      state === 'changes' ? 'Changes were requested on the Branch card' : state === 'rejected' ? 'The Branch card was rejected' : id ? `Branch approval #${id} is waiting for you${scrollable ? ' (click to find it in the chat)' : ''}` : "No 'Branch:' card requested yet"
    const badge = (
      <Badge data-branch-chip="unconfirmed" className="bg-amber-50 px-2 text-amber-800">
        branch not confirmed
      </Badge>
    )
    return (
      <Tip content={why}>
        {id && scrollable ? (
          <button
            type="button"
            data-branch-link
            className="cursor-pointer rounded-full"
            onClick={() => document.querySelector(`[data-card="approval-${id}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })}
          >
            {badge}
          </button>
        ) : (
          <span>{badge}</span>
        )}
      </Tip>
    )
  }
  if (mode === 'current') {
    return (
      <Tip content="Works in the main folder, on the branch that was checked out when the Master approved">
        <span>
          <Badge data-branch-chip="current" className="px-2 text-gray-700">
            current folder{branch.branch ? ` (${branch.branch})` : ''}
          </Badge>
        </span>
      </Tip>
    )
  }
  const tip = [branch.tree ? tildify(branch.tree) : null, mode === 'shared' ? 'shared worktree' : branch.base ? `from ${branch.base}` : null].filter(Boolean).join(' · ')
  return (
    <Tip content={tip}>
      <span>
        <Badge data-branch-chip={mode ?? 'new'} className="px-2 font-mono text-gray-700">
          <GitBranch />
          {branch.branch}
        </Badge>
      </span>
    </Tip>
  )
}
