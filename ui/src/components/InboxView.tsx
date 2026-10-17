import type { Inbox, InboxMessage, Question } from '../api'
import ApprovalCard from './ApprovalCard'
import CommitCard from './CommitCard'
import MessageRow from './MessageRow'
import QuestionCard from './QuestionCard'
import { featureLabel, useCards } from './CardContext'
import { roleName } from '../roles'

function Group({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  if (!count) return null
  return (
    <section className="mb-5">
      <div className="px-4 mb-1 text-[11px] uppercase tracking-wide text-gray-500 font-semibold">
        {title} <span className="text-gray-400">({count})</span>
      </div>
      {children}
    </section>
  )
}

export default function InboxView({
  inbox,
  onAnswer,
  onOpen,
  onResolve,
}: {
  inbox: Inbox | null
  onAnswer: (q: Question) => (answers: Record<string, string>) => Promise<void>
  onOpen: (conversationId: string) => void
  onResolve: (m: InboxMessage) => Promise<void>
}) {
  const ctx = useCards()
  if (!inbox) return <div className="flex-1 px-4 py-6 text-gray-400 text-[13px]">Loading…</div>
  const total = inbox.questions.length + inbox.approvals.length + inbox.commits.length + inbox.messages.length
  if (!total) return <div className="flex-1 px-4 py-6 text-gray-500 text-[13px]">Nothing needs you.</div>
  const commits = inbox.commits.filter((c) => c.state === 'master_review')

  return (
    <div className="flex-1 overflow-y-auto py-3">
      <Group title="Questions" count={inbox.questions.length}>
        {inbox.questions.map((q) => (
          <QuestionCard key={`${q.id}-${q.state}`} q={q} onAnswer={onAnswer(q)} />
        ))}
      </Group>
      <Group title="Approvals" count={inbox.approvals.length}>
        {inbox.approvals.map((a) => (
          <div key={a.id} className="mx-4 my-2">
            <ApprovalCard a={a} showFeature />
          </div>
        ))}
      </Group>
      <Group title="Commits" count={commits.length}>
        {commits.map((c) => (
          <div key={c.id} className="mx-4 my-2">
            <CommitCard c={c} showFeature />
          </div>
        ))}
      </Group>
      <Group title="Messages" count={inbox.messages.length}>
        {inbox.messages.map((m) => {
          const feat = ctx ? ctx.features.find((f) => f.conversation_id === m.conversation_id) : undefined
          const where = feat ? featureLabel(ctx!.features, feat.id) : m.conversation_title || m.conversation_id
          return (
            <div key={m.id} className="mx-4 my-2 rounded-md border border-gray-200">
              <div className="flex items-center gap-2 px-4 pt-2 text-xs text-gray-500">
                <span>
                  {roleName(m.author)} in <span className="font-medium text-gray-700">{where}</span>
                </span>
                <button onClick={() => onOpen(m.conversation_id)} className="ml-auto text-blue-600 hover:underline">
                  Open
                </button>
                <button onClick={() => void onResolve(m)} className="rounded-md border border-gray-300 px-2 py-0.5 text-gray-700 hover:bg-gray-50">
                  Resolve
                </button>
              </div>
              <MessageRow m={m} />
            </div>
          )
        })}
      </Group>
    </div>
  )
}
