import type { Message } from '../api'
import { fmtTime } from '../api'
import { roleName, TYPE_CHIP } from '../roles'
import ApprovalCard from './ApprovalCard'
import ArtifactCard from './ArtifactCard'
import Avatar from './Avatar'
import CommitCard from './CommitCard'
import Markdown from './Markdown'
import MockPreviews from './MockPreviews'
import QuestionCard from './QuestionCard'
import Reactions from './Reactions'
import Receipts from './Receipts'
import { Badge } from './ui/badge'

export default function MessageRow({ m, highlight = false }: { m: Message; highlight?: boolean }) {
  const question = m.ref?.startsWith('question:') && m.question ? m.question : null
  const artifact = m.ref?.startsWith('artifact:') && m.artifact ? m.artifact : null
  const card = m.ref?.startsWith('approval:') && m.approval ? <ApprovalCard a={m.approval} /> : m.ref?.startsWith('commit:') && m.commit ? <CommitCard c={m.commit} /> : question ? <QuestionCard key={`${question.id}-${question.state}`} q={question} embedded /> : artifact ? <ArtifactCard a={artifact} /> : null
  return (
    <div id={`m-${m.id}`} className={`flex gap-2.5 px-4 py-1.5 ${highlight ? 'bg-amber-50' : 'hover:bg-gray-50'}`}>
      <Avatar author={m.author} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          {/* F-31: the name truncates before the time, the chip or the number ever wrap or get pushed out */}
          <span className="min-w-0 truncate font-bold">{roleName(m.author)}</span>
          <span className="shrink-0 text-caption text-gray-400">{fmtTime(m.created_at)}</span>
          {m.type && m.type !== 'Message' && (
            <Badge className={TYPE_CHIP[m.type] || TYPE_CHIP.System}>{m.type}</Badge>
          )}
          {/* the number roles cite (#1268, ...): always visible, at the right end of the header line */}
          <span data-msg-id className="ml-auto shrink-0 self-center text-micro text-gray-400 tabular-nums select-text">
            #{m.id}
          </span>
        </div>
        {/* F-10: a card message's text is the same questions in plain Markdown (for roles); the card replaces it. */}
        {!question && <Markdown text={m.text} />}
        {!question && !artifact && <MockPreviews text={m.text} />}
        {card}
        <Reactions reactions={m.reactions} />
        <Receipts receipts={m.receipts} />
      </div>
    </div>
  )
}
