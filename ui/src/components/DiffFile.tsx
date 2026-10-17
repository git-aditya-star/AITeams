import { useMemo } from 'react'
import type { DiffFile as DiffFileT } from '../api'
import { fmtSize, parseFileDiff } from '../diff'

const LARGE = 300

// Built, binary, unchanged, missing and large files start folded.
export const foldedByDefault = (f: DiffFileT) =>
  f.built || f.binary || f.status === 'unchanged' || f.status === 'missing' || f.additions + f.deletions > LARGE

const BADGE: Record<string, string> = {
  added: 'bg-green-50 text-green-700',
  deleted: 'bg-red-50 text-red-700',
  missing: 'bg-red-50 text-red-700',
  binary: 'bg-gray-100 text-gray-600',
  unchanged: 'bg-gray-100 text-gray-600',
}
const BADGE_LABEL: Record<string, string> = { added: 'new', deleted: 'deleted', missing: 'missing', binary: 'binary', unchanged: 'unchanged' }

export function StatusBadge({ status }: { status: string }) {
  if (!BADGE[status]) return null
  return <span className={`text-[11px] rounded-full px-2 py-px font-medium ${BADGE[status]}`}>{BADGE_LABEL[status]}</span>
}

export const Counts = ({ a, d }: { a: number; d: number }) => (
  <span className="font-mono text-xs whitespace-nowrap">
    <span className="text-green-700">+{a}</span> <span className="text-red-700">−{d}</span>
  </span>
)

const ROW: Record<string, string> = { add: 'bg-green-50', del: 'bg-red-50', ctx: '' }
const SIGN: Record<string, string> = { add: '+', del: '-', ctx: ' ' }
const SIGN_CLASS: Record<string, string> = { add: 'text-green-800', del: 'text-red-800', ctx: 'text-gray-400' }
const num = 'select-none text-right text-gray-400 px-2 align-top w-px'

export default function DiffFile({ f, open, onToggle }: { f: DiffFileT; open: boolean; onToggle: () => void }) {
  const hunks = useMemo(() => (open ? parseFileDiff(f.text) : []), [open, f.text])
  const changed = f.additions + f.deletions
  const note = f.status === 'missing' ? 'not on disk' : f.built ? `built file, ${fmtSize(f.size)}` : changed > LARGE && !f.binary ? `large diff, ${changed} lines` : null

  return (
    <section id={`diff-${f.path}`} className="rounded-md border border-gray-200 bg-white">
      <button
        onClick={onToggle}
        className={`sticky top-0 z-10 w-full flex items-center gap-2 px-3 py-1.5 bg-gray-50 text-left text-[13px] ${open ? 'border-b border-gray-200 rounded-t-md' : 'rounded-md'}`}
      >
        <span className="text-gray-400 w-3">{open ? '▾' : '▸'}</span>
        <span className="font-mono text-xs text-gray-800 truncate">{f.path}</span>
        <StatusBadge status={f.status} />
        {f.binary && <StatusBadge status="binary" />}
        {note && <span className={`text-xs ${f.status === 'missing' ? 'text-red-700' : 'text-gray-500'}`}>{note}</span>}
        <span className="ml-auto">
          <Counts a={f.additions} d={f.deletions} />
        </span>
      </button>
      {open && (
        <div className="overflow-x-auto">
          {hunks.length === 0 ? (
            <div className="px-3 py-2 text-xs text-gray-500">
              {f.status === 'missing' ? (
                <span className="text-red-700">File not on disk; committing will fail.</span>
              ) : f.binary ? 'Binary file, no text diff.' : f.status === 'unchanged' ? 'No changes against HEAD.' : 'No text diff.'}
            </div>
          ) : (
            <table className="min-w-full font-mono text-[12px] leading-[18px] border-collapse">
              <tbody>
                {hunks.map((h, i) => [
                  <tr key={`h${i}`} className="bg-blue-50 text-blue-700">
                    <td colSpan={4} className="px-3 py-0.5 whitespace-pre">
                      {h.header}
                      {h.context && <span className="ml-2 text-blue-600/80">{h.context}</span>}
                    </td>
                  </tr>,
                  ...h.lines.map((l, j) => (
                    <tr key={`${i}-${j}`} className={ROW[l.kind]}>
                      <td className={num}>{l.old ?? ''}</td>
                      <td className={num}>{l.new ?? ''}</td>
                      <td className={`select-none w-px pl-1 pr-1 ${SIGN_CLASS[l.kind]}`}>{SIGN[l.kind]}</td>
                      <td className="pr-3 whitespace-pre text-gray-800">{l.text}</td>
                    </tr>
                  )),
                ])}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  )
}
