import type { Receipt, ReceiptState } from '../api'
import Avatar from './Avatar'
import { Check, Clock, Eye } from './icons'

const GROUPS: { state: ReceiptState; Icon: typeof Check; title: string }[] = [
  { state: 'acked', Icon: Check, title: 'acknowledged' },
  { state: 'seen', Icon: Eye, title: 'seen' },
  { state: 'queued', Icon: Clock, title: 'queued' },
]

export default function Receipts({ receipts }: { receipts: Receipt[] }) {
  if (!receipts?.length) return null
  return (
    <div className="flex items-center gap-3 mt-1 select-none">
      {GROUPS.map((g) => {
        const roles = receipts.filter((r) => r.state === g.state).map((r) => r.role)
        if (!roles.length) return null
        const shown = roles.slice(0, 3)
        const extra = roles.length - shown.length
        return (
          <div key={g.state} className="flex items-center" title={`${g.title}: ${roles.join(', ')}`}>
            <g.Icon size={12} className="text-gray-400 mr-1" />
            <div className="flex">
              {shown.map((r, i) => (
                <Avatar key={r} author={r} size={18} className={`ring-2 ring-white ${i ? '-ml-1.5' : ''}`} />
              ))}
              {extra > 0 && (
                <span className="inline-flex items-center justify-center rounded-full bg-gray-200 text-gray-600 ring-2 ring-white -ml-1.5 text-[10px] font-medium" style={{ width: 18, height: 18 }}>
                  +{extra}
                </span>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
