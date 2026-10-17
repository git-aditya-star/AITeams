import type { Message } from '../api'
import { fmtTime } from '../api'
import { roleName, TYPE_CHIP } from '../roles'
import Avatar from './Avatar'
import Markdown from './Markdown'
import Receipts from './Receipts'

export default function MessageRow({ m }: { m: Message }) {
  return (
    <div className="flex gap-2.5 px-4 py-1.5 hover:bg-gray-50">
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
        <Receipts receipts={m.receipts} />
      </div>
    </div>
  )
}
