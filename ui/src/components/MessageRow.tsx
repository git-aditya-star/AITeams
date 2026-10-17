import type { Message } from '../api'
import { fmtTime } from '../api'
import { roleName, TYPE_CHIP } from '../roles'
import ApprovalCard from './ApprovalCard'
import Avatar from './Avatar'
import CommitCard from './CommitCard'
import Markdown from './Markdown'
import Receipts from './Receipts'

export default function MessageRow({ m, highlight = false }: { m: Message; highlight?: boolean }) {
  const card = m.ref?.startsWith('approval:') && m.approval ? <ApprovalCard a={m.approval} /> : m.ref?.startsWith('commit:') && m.commit ? <CommitCard c={m.commit} /> : null
  return (
    <div id={`m-${m.id}`} className={`flex gap-2.5 px-4 py-1.5 ${highlight ? 'bg-amber-50' : 'hover:bg-gray-50'}`}>
      <Avatar author={m.author} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-bold">{roleName(m.author)}</span>
          <span className="text-xs text-gray-400">{fmtTime(m.created_at)}</span>
          {m.type && m.type !== 'Message' && (
            <span className={`text-[11px] rounded-full px-2 py-px font-medium ${TYPE_CHIP[m.type] || TYPE_CHIP.System}`}>{m.type}</span>
          )}
        </div>
        <Markdown text={m.text} />
        {card}
        <Receipts receipts={m.receipts} />
      </div>
    </div>
  )
}
