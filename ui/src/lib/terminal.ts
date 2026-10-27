// F-33: pure helpers that turn a stream-json tool call and its result into the lines a Claude Code terminal shows
// (`⏺ Read(path)`, `⎿ Read 96 lines`, a red/green diff). No React, so the rules are easy to read in review and to test.

export type ToolResult = {
  text: string // the tool_result content, as text
  isError: boolean
  denied: boolean // our PreToolUse hook refused the call (`AITEAMS_DENY: <reason>`)
  data?: unknown // the event's top-level `tool_use_result` (Edit's structuredPatch, Read's numLines, ...)
}
export type Line = { title: string; arg: string; dim: string } // `⏺ title(arg) dim`; no parentheses when arg is empty
export type Summary = { text: string; more: string; list: string[] } // `⎿ text more`, then one line per list item
export type DiffRow = { sign: '+' | '-' | ' '; line: number | null; text: string }
export type Body = { md?: string; files?: string[] } // what an AITeams tool shows under its line

const AITEAMS = 'mcp__aiteams__'
export const DENY = 'AITEAMS_DENY'

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const rec = (v: unknown): Record<string, any> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, any>) : {})
const arr = (v: unknown): any[] => (Array.isArray(v) ? v : [])
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`
const cut = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)
const lines = (s: string) => (s === '' ? [] : s.replace(/\n$/, '').split('\n'))
const firstLine = (s: string) => lines(s).find((l) => l.trim()) ?? ''

export const lineCount = (s: string) => lines(s).length
export const aiteamsTool = (name: string) => (name.startsWith(AITEAMS) ? name.slice(AITEAMS.length) : null)

// A path inside the project root is shown relative to it (the root is the `cwd` of the turn's init event).
export function relPath(p: string, root?: string | null): string {
  const r = (root ?? '').replace(/\/+$/, '')
  if (!r) return p
  return p === r ? '.' : p.startsWith(`${r}/`) ? p.slice(r.length + 1) : p
}

export function deniedReason(text: string): string {
  const i = text.indexOf(DENY)
  return (i < 0 ? text : text.slice(i + DENY.length)).replace(/^[:\s]+/, '').replace(/\s+/g, ' ').trim()
}

const firstStringArg = (i: Record<string, any>) => cut(firstLine(Object.values(i).find((v) => typeof v === 'string' && v.trim()) ?? ''), 80)
const words = (s: string) => {
  const w = s.replace(/_/g, ' ')
  return w.charAt(0).toUpperCase() + w.slice(1)
}

// ---------------------------------------------------------------- the `⏺` line

export function toolLine(name: string, input: unknown, root?: string | null): Line {
  const i = rec(input)
  const L = (title: string, arg = '', dim = ''): Line => ({ title, arg, dim })
  const a = aiteamsTool(name)
  if (a) return aiteamsLine(a, i)
  const path = relPath(str(i.file_path) || str(i.notebook_path), root)
  switch (name) {
    case 'Read':
      return L('Read', path)
    case 'Edit':
    case 'MultiEdit':
    case 'NotebookEdit':
      return L('Update', path)
    case 'Write':
      return L('Write', path)
    case 'Bash':
      return L('Bash', cut(firstLine(str(i.command)), 80))
    case 'Grep':
      return L('Grep', [`"${str(i.pattern)}"`, i.path ? relPath(str(i.path), root) : ''].filter(Boolean).join(', '))
    case 'Glob':
      return L('Glob', str(i.pattern))
    case 'WebFetch':
      return L('WebFetch', str(i.url))
    case 'WebSearch':
      return L('WebSearch', `"${str(i.query)}"`)
    case 'Agent':
    case 'Task':
      return L(name, cut(str(i.description), 80))
    case 'Skill':
      return L('Skill', str(i.skill) || str(i.name))
    case 'ToolSearch':
      return L('ToolSearch', str(i.query))
    case 'TodoWrite':
      return L('Update Todos')
  }
  const mcp = /^mcp__(.+?)__(.+)$/.exec(name) // another MCP server's tool, as Claude Code writes it
  if (mcp) return L(`${mcp[1]} - ${mcp[2]} (MCP)`, firstStringArg(i))
  return L(name, firstStringArg(i))
}

function aiteamsLine(a: string, i: Record<string, any>): Line {
  const L = (title: string, arg = '', dim = ''): Line => ({ title, arg, dim })
  switch (a) {
    case 'post_message': {
      const type = str(i.type)
      const to = arr(i.mentions).map((m) => `@${m}`)
      const dim = [type && type !== 'Message' ? `· ${type}` : '', to.length ? `→ ${to.join(' ')}` : ''].filter(Boolean).join(' ')
      return L(`Posted in ${str(i.conversation)}`, '', dim)
    }
    case 'ask_master': {
      const qs = arr(i.questions)
      const heads = qs.map((q) => str(rec(q).header)).filter(Boolean)
      return L(`Asked the Master: ${plural(qs.length, 'question')}`, '', heads.length ? `(${heads.join(', ')})` : '')
    }
    case 'request_approval':
      return L(`Asked for approval: ${str(i.title)}`)
    case 'request_commit':
      return L(`Commit request: ${firstLine(str(i.message))}`, '', `(${plural(arr(i.files).length, 'file')})`)
    case 'review_commit':
      return L(`${str(i.decision) === 'reject' ? 'Rejected' : 'Approved'} commit #${i.commit}`)
    case 'react':
      return L(`Reacted ${str(i.emoji)} to #${i.message_id}`)
    case 'set_feature': {
      const set = ['phase', 'owner', 'lead', 'title'].filter((k) => i[k] != null).map((k) => `${k} ${i[k]}`)
      return L(`Set ${str(i.feature)}${set.length ? `: ${set.join(', ')}` : ''}`)
    }
    case 'write_handoff':
      return L('Wrote handoff')
    default:
      return L(words(a), firstStringArg(i)) // get_feature -> "Get feature(F-33)"
  }
}

// What an AITeams tool shows under its line: a posted message as text, a commit's file list.
export function toolBody(name: string, input: unknown): Body | null {
  const a = aiteamsTool(name)
  const i = rec(input)
  if (a === 'post_message' && str(i.text)) return { md: str(i.text) }
  if (a === 'request_commit' && arr(i.files).length) return { files: arr(i.files).map(String) }
  if (a === 'review_commit' && str(i.note)) return { md: str(i.note) }
  return null
}

// ---------------------------------------------------------------- the `⎿` line

// The first line of a failed call. A Bash failure starts with "Exit code N": the line after it is the useful one.
function errorSummary(text: string): Summary {
  const ls = lines(text).filter((l) => l.trim())
  const first = ls[0] ?? 'error'
  const joined = /^Exit code \d+/.test(first) && ls[1]
  const shown = joined ? `${first}: ${ls[1].trim()}` : first
  const rest = ls.length - (joined ? 2 : 1)
  return { text: cut(shown, 200), more: rest > 0 ? `+${plural(rest, 'line')}` : '', list: [] }
}

function firstLineSummary(text: string): Summary {
  const ls = lines(text)
  const rest = ls.length - 1
  return { text: cut(firstLine(text), 200), more: rest > 0 ? `+${plural(rest, 'line')}` : '', list: [] }
}

// `⎿` summary for a call, or null when the tool shows nothing under its line (react, set_feature, ...).
export function toolSummary(name: string, input: unknown, result: ToolResult | undefined, running: boolean, root?: string | null): Summary | null {
  const none = (text: string): Summary => ({ text, more: '', list: [] })
  if (!result) return none(running ? 'Running…' : 'No result')
  if (result.denied) return none(`Denied by hook: ${deniedReason(result.text)}`)
  if (result.isError) return errorSummary(result.text)
  const i = rec(input)
  const a = aiteamsTool(name)
  if (a) {
    const id = /#(\d+)/.exec(result.text)?.[1]
    switch (a) {
      case 'post_message':
      case 'request_commit':
        return id ? none(`#${id}`) : firstLineSummary(result.text)
      case 'ask_master':
        return id ? none(`card #${id}`) : firstLineSummary(result.text)
      case 'request_approval':
        return id ? none(`approval #${id}`) : firstLineSummary(result.text)
      case 'review_commit':
      case 'react':
      case 'set_feature':
      case 'write_handoff':
        return null
      default:
        return firstLineSummary(result.text)
    }
  }
  const path = relPath(str(i.file_path) || str(i.notebook_path), root)
  const data = rec(result.data)
  switch (name) {
    case 'Read':
      return none(`Read ${plural(Number(rec(data.file).numLines) || lineCount(result.text), 'line')}`)
    case 'Edit':
    case 'MultiEdit':
    case 'NotebookEdit': {
      const { add, rem } = diffCounts(editDiff(input, result.data))
      return none(`Updated ${path} with ${plural(add, 'addition')} and ${plural(rem, 'removal')}`)
    }
    case 'Write':
      return none(`Wrote ${plural(lineCount(str(i.content)), 'line')} to ${path}`)
    case 'Bash':
      return result.text.trim() ? firstLineSummary(result.text) : none('(No content)')
    case 'Grep': {
      if (data.mode === 'files_with_matches' && Number.isFinite(Number(data.numFiles))) return none(`Found ${plural(Number(data.numFiles), 'file')}`)
      return none(`Found ${plural(/^No (matches|files)/i.test(result.text.trim()) ? 0 : lineCount(result.text), 'line')}`)
    }
    case 'Glob':
      return none(`Found ${plural(Number(data.numFiles) || (/^No files/i.test(result.text.trim()) ? 0 : lineCount(result.text)), 'file')}`)
    case 'TodoWrite':
      return { text: '', more: '', list: arr(i.todos).map((t) => `${rec(t).status === 'completed' ? '☒' : '☐'} ${str(rec(t).content)}`) }
    default:
      return firstLineSummary(result.text)
  }
}

// Whether a click can show more than the summary does: the full output of a call (not a diff, a posted message or a todo list).
export function expandable(name: string, input: unknown, result: ToolResult | undefined): boolean {
  if (!result || result.denied || !result.text.trim()) return false
  if (/^(Edit|MultiEdit|NotebookEdit|Write|TodoWrite)$/.test(name) && !result.isError) return false
  if (toolBody(name, input) && !result.isError) return false
  return lineCount(result.text) > 1 || result.text.length > 120
}

// ---------------------------------------------------------------- diffs

// The change an Edit made, as rows. Claude Code's own `structuredPatch` (real line numbers and context) when the result
// carries it; otherwise every old_string line removed and every new_string line added, with no line numbers.
export function editDiff(input: unknown, data?: unknown): DiffRow[] {
  const patch = arr(rec(data).structuredPatch)
  if (patch.length) {
    const rows: DiffRow[] = []
    patch.forEach((h, k) => {
      if (k > 0) rows.push({ sign: ' ', line: null, text: '…' })
      let o = Number(rec(h).oldStart) || 1
      let n = Number(rec(h).newStart) || 1
      for (const raw of arr(rec(h).lines)) {
        const l = String(raw)
        if (l.startsWith('\\')) continue // "\ No newline at end of file"
        if (l[0] === '-') rows.push({ sign: '-', line: o++, text: l.slice(1) })
        else if (l[0] === '+') rows.push({ sign: '+', line: n++, text: l.slice(1) })
        else {
          rows.push({ sign: ' ', line: n, text: l.slice(1) })
          o++
          n++
        }
      }
    })
    return rows
  }
  const i = rec(input)
  const rows: DiffRow[] = []
  for (const e of Array.isArray(i.edits) ? i.edits : [i]) {
    for (const t of lines(str(rec(e).old_string))) rows.push({ sign: '-', line: null, text: t })
    for (const t of lines(str(rec(e).new_string))) rows.push({ sign: '+', line: null, text: t })
  }
  return rows
}

export function diffCounts(rows: DiffRow[]): { add: number; rem: number } {
  return { add: rows.filter((r) => r.sign === '+').length, rem: rows.filter((r) => r.sign === '-').length }
}

// A Write's content as added rows, numbered from 1.
export function writeRows(input: unknown): DiffRow[] {
  return lines(str(rec(input).content)).map((text, k) => ({ sign: '+' as const, line: k + 1, text }))
}
