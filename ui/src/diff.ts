// Per-file unified diff → hunks with line numbers (F-3). Pure: no React, no DOM.

export type DiffLine = { kind: 'add' | 'del' | 'ctx'; old?: number; new?: number; text: string }
export type Hunk = { header: string; context: string; lines: DiffLine[] }

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$/

// `text` is one file's slice of `git diff`. Git header lines before the first `@@` are metadata and skipped.
export function parseFileDiff(text: string): Hunk[] {
  const hunks: Hunk[] = []
  let cur: Hunk | null = null
  let oldNo = 0
  let newNo = 0
  const lines = text.split('\n')
  if (lines[lines.length - 1] === '') lines.pop()
  for (const line of lines) {
    const m = HUNK_RE.exec(line)
    if (m) {
      oldNo = Number(m[1])
      newNo = Number(m[2])
      cur = { header: line.slice(0, line.length - m[3].length).trimEnd(), context: m[3], lines: [] }
      hunks.push(cur)
      continue
    }
    if (!cur || line.startsWith('\\')) continue // metadata before the first hunk, "\ No newline at end of file"
    const sign = line[0]
    const body = line.slice(1)
    if (sign === '+') cur.lines.push({ kind: 'add', new: newNo++, text: body })
    else if (sign === '-') cur.lines.push({ kind: 'del', old: oldNo++, text: body })
    else cur.lines.push({ kind: 'ctx', old: oldNo++, new: newNo++, text: body })
  }
  return hunks
}

export const fmtSize = (bytes: number | null | undefined) =>
  bytes == null ? '' : bytes >= 1024 ? `${Math.round(bytes / 1024)} KB` : `${bytes} B`
