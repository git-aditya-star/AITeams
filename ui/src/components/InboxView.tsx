import ApprovalCard from './ApprovalCard'
import CommitCard from './CommitCard'
import CommitGroupCard from './CommitGroupCard'
import MessageRow from './MessageRow'
import QuestionCard from './QuestionCard'
import { roleName } from '../roles'
import { useAppDispatch, useAppSelector } from '../store'
import { featureLabel } from '../store/selectors'
import { resolveInboxMessage } from '../store/thunks'
import { Button } from './ui/button'

function Group({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  if (!count) return null
  return (
    <section className="mb-5">
      <div className="px-4 mb-1 text-caption uppercase tracking-wide text-gray-500 font-semibold">
        {title} <span className="text-gray-400">({count})</span>
      </div>
      {children}
    </section>
  )
}

export default function InboxView({ onOpen }: { onOpen: (conversationId: string) => void }) {
  const dispatch = useAppDispatch()
  const inbox = useAppSelector((s) => s.inbox.data)
  const features = useAppSelector((s) => s.features)
  if (!inbox) return <div className="flex-1 px-4 py-6 text-gray-400 text-meta">Loading…</div>
  const groups = inbox.groups ?? []
  const total = inbox.questions.length + inbox.approvals.length + inbox.commits.length + groups.length + inbox.messages.length
  if (!total) return <div className="flex-1 px-4 py-6 text-gray-500 text-meta">Nothing needs you.</div>
  const commits = inbox.commits.filter((c) => c.state === 'master_review')

  return (
    <div className="flex-1 overflow-y-auto py-3">
      <Group title="Questions" count={inbox.questions.length}>
        {inbox.questions.map((q) => (
          <QuestionCard key={`${q.id}-${q.state}`} q={q} />
        ))}
      </Group>
      <Group title="Approvals" count={inbox.approvals.length}>
        {inbox.approvals.map((a) => (
          <div key={a.id} className="mx-4 my-2">
            <ApprovalCard a={a} showFeature />
          </div>
        ))}
      </Group>
      <Group title="Commits" count={commits.length + groups.length}>
        {groups.map((g) => (
          <div key={`g-${g.id}`} className="mx-4 my-2">
            <CommitGroupCard g={g} />
          </div>
        ))}
        {commits.map((c) => (
          <div key={c.id} className="mx-4 my-2">
            <CommitCard c={c} showFeature />
          </div>
        ))}
      </Group>
      <Group title="Messages" count={inbox.messages.length}>
        {inbox.messages.map((m) => {
          const feat = features.find((f) => f.conversation_id === m.conversation_id)
          const where = feat ? featureLabel(features, feat.id) : m.conversation_title || m.conversation_id
          return (
            <div key={m.id} className="mx-4 my-2 rounded-md border border-gray-200">
              <div className="flex items-center gap-2 px-4 pt-2 text-caption text-gray-500">
                <span>
                  {roleName(m.author)} in <span className="font-medium text-gray-700">{where}</span>
                </span>
                <Button variant="link" size="inline" className="ml-auto text-caption" onClick={() => onOpen(m.conversation_id)}>
                  Open
                </Button>
                <Button variant="outline" size="xs" onClick={() => void dispatch(resolveInboxMessage(m))}>
                  Resolve
                </Button>
              </div>
              <MessageRow m={m} markMaster={false} />
            </div>
          )
        })}
      </Group>
    </div>
  )
}
