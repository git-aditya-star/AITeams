import { GitMerge, GitPullRequest, GitPullRequestClosed, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import type { FeatureBranch, PRState } from '../api'
import { api } from '../api'
import { useAppDispatch, useAppSelector } from '../store'
import { featureUpserted } from '../store/actions'
import { selectPid } from '../store/selectors'
import { prNumber, webUrl } from './PRCard'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Tip } from './ui/tooltip'

// F-39: the feature's pull request, in the feature header next to the branch chip: a link to it on GitHub with its state, the
// worktree's fate in the tooltip, and (while it is open) a refresh button that asks GitHub now instead of at the next 10-minute poll.
// A feature with no pull request shows nothing.
const STATE: Record<PRState, { label: string; chip: string; Icon: typeof GitPullRequest }> = {
  open: { label: 'open', chip: 'bg-green-50 text-green-800', Icon: GitPullRequest },
  merged: { label: 'merged', chip: 'bg-purple-50 text-purple-800', Icon: GitMerge },
  closed: { label: 'closed', chip: 'bg-gray-100 text-gray-700', Icon: GitPullRequestClosed },
}
const TREE_NOTE = { removed: 'worktree removed', kept: "worktree kept: it has changes git won't delete" } as const

export default function PRChip({ branch, featureId }: { branch: FeatureBranch | undefined; featureId: number }) {
  const dispatch = useAppDispatch()
  const pid = useAppSelector(selectPid)
  const [busy, setBusy] = useState(false)
  const url = webUrl(branch?.pr_url)
  if (!branch || !url) return null
  const state = branch.pr_state && STATE[branch.pr_state] ? branch.pr_state : 'open'
  const { label, chip, Icon } = STATE[state]
  const n = prNumber(url)
  const tip = [branch.branch && branch.pr_destination ? `${branch.branch} → ${branch.pr_destination}` : null, branch.tree_state && branch.tree_state !== 'live' ? TREE_NOTE[branch.tree_state] : null]
    .filter(Boolean)
    .join(' · ')

  const refresh = async () => {
    if (!pid) return
    setBusy(true)
    try {
      dispatch(featureUpserted(await api.refreshPr(pid, featureId)))
    } catch (e: any) {
      // GitHub could not be asked (no `gh`, not logged in, offline): the daemon's sentence, not the raw request line.
      toast.error(typeof e?.detail === 'string' ? e.detail : e?.message || 'Could not check the pull request')
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="inline-flex items-center gap-0.5">
      <Tip content={tip || url}>
        <Badge asChild className={`px-2 ${chip}`}>
          <a data-pr-chip={state} href={url} target="_blank" rel="noreferrer noopener">
            <Icon />
            PR{n ? ` #${n}` : ''} · {label}
          </a>
        </Badge>
      </Tip>
      {state === 'open' && (
        <Tip content="Check the pull request on GitHub now">
          <Button variant="ghost" size="icon-xs" data-pr-refresh aria-label="Check the pull request on GitHub now" disabled={busy} onClick={refresh}>
            <RefreshCw className={busy ? 'animate-spin' : ''} />
          </Button>
        </Tip>
      )}
    </span>
  )
}
