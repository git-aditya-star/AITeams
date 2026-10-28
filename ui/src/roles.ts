import type { Role } from './api'

export const ROLES: Role[] = ['manager', 'lead', 'lead-2', 'product', 'frontend', 'backend', 'qa']

// F-22: two Leads. Lead 1 keeps the internal id `lead`, so nothing in flight is renamed; people see "Lead 1" / "Lead 2".
// A feature belongs to one of them (`Feature.lead`).
export const LEADS: Role[] = ['lead', 'lead-2']
export const isLead = (r: string) => (LEADS as string[]).includes(r)

const NAMES: Record<string, string> = { master: 'Master', lead: 'Lead 1', 'lead-2': 'Lead 2' }
export const roleName = (r: string) => NAMES[r] || r.charAt(0).toUpperCase() + r.slice(1)

// F-41: a role's session for one feature reads "Frontend · F-36"; its main session (lane '' or none) is just "Frontend".
export const laneLabel = (role: string, lane?: string | null) => (lane ? `${roleName(role)} · ${lane}` : roleName(role))

// What a person types after the @: `@lead-1` is an alias for `lead`, `@lead-2` is Lead 2 (the daemon's MENTION_RE
// accepts `@name-<digit>`). A bare `@lead` still works; in a feature it reaches that feature's Lead.
export const MENTIONABLE = ROLES.map((r) => (r === 'lead' ? 'lead-1' : r))
export const mentionRole = (handle: string) => (handle === 'lead-1' ? 'lead' : handle)

// Fixed pastel avatar colours per role; Master is dark blue.
const AVATAR: Record<string, string> = {
  master: 'bg-blue-800 text-white',
  manager: 'bg-rose-200 text-rose-900',
  lead: 'bg-violet-200 text-violet-900',
  'lead-2': 'bg-violet-300 text-violet-950',
  product: 'bg-amber-200 text-amber-900',
  frontend: 'bg-sky-200 text-sky-900',
  backend: 'bg-emerald-200 text-emerald-900',
  qa: 'bg-orange-200 text-orange-900',
}
export const avatarClass = (author: string) => AVATAR[author] || 'bg-gray-200 text-gray-800'
const INITIAL: Record<string, string> = { master: 'M', lead: 'L1', 'lead-2': 'L2' }
export const avatarInitial = (author: string) => INITIAL[author] || author.charAt(0).toUpperCase()

export const TYPE_CHIP: Record<string, string> = {
  Question: 'bg-blue-50 text-blue-700',
  Decision: 'bg-emerald-50 text-emerald-700',
  Concern: 'bg-red-50 text-red-700',
  Finding: 'bg-violet-50 text-violet-700',
  Handoff: 'bg-amber-50 text-amber-800',
  System: 'bg-gray-100 text-gray-600',
  Blocker: 'bg-red-100 text-red-800',
  Approval: 'bg-amber-50 text-amber-800',
  Commit: 'bg-teal-50 text-teal-700',
  Standup: 'bg-indigo-50 text-indigo-700',
}

export const dotClass = (state: string) =>
  state === 'running'
    ? 'bg-green-500 animate-pulse'
    : state === 'queued'
      ? 'bg-amber-400'
      : 'bg-gray-300'

// State word for a role: the running turn's kind wins over the plain state.
export const stateWord = (t: { state: string; kind?: string | null } | undefined) => {
  if (!t) return 'idle'
  if (t.state === 'running' && t.kind === 'handoff') return 'handoff'
  if (t.state === 'running' && (t.kind === 'standup' || t.kind === 'standup_compile')) return 'standup'
  if (t.state === 'running' && (t.kind === 'brainstorm' || t.kind === 'brainstorm_compile')) return 'brainstorm'
  return t.state
}

// F-42: the cut-offs are relative to the rotation limit (150k tokens): amber from 70% (105k), red from 90% (135k), i.e. rotation is close.
export const meterClass = (pct: number) =>
  pct >= 0.9 ? 'bg-red-500' : pct >= 0.7 ? 'bg-amber-400' : 'bg-green-500'

// F-42: `context_pct` reads against the rotation limit (150k tokens), so an old session can read 395% until its next turn ends and
// it rotates. Every place that prints or draws it shows at most 100%; at 100% the tooltip says when it rotates.
type Meter = { state?: string; context_pct?: number | null }
export const contextPct = (t: Meter | undefined) => Math.min(1, Math.max(0, t?.context_pct || 0))
export const contextNote = (t: Meter | undefined) =>
  contextPct(t) < 1 ? '' : t?.state === 'running' ? 'rotates after this turn' : 'rotates after its next turn'

export const PHASES = ['intake', 'spec', 'design', 'build', 'qa', 'done'] as const

export const PHASE_CHIP: Record<string, string> = {
  intake: 'bg-gray-100 text-gray-600',
  spec: 'bg-blue-50 text-blue-700',
  design: 'bg-violet-50 text-violet-700',
  build: 'bg-amber-50 text-amber-800',
  qa: 'bg-orange-50 text-orange-700',
  done: 'bg-emerald-50 text-emerald-700',
}
export const phaseChip = (phase: string) => PHASE_CHIP[phase] || PHASE_CHIP.intake

// F-18: solid tones of PHASE_CHIP for the Dashboard's phase bar.
export const PHASE_FILL: Record<string, string> = {
  intake: 'bg-gray-400',
  spec: 'bg-blue-500',
  design: 'bg-violet-500',
  build: 'bg-amber-500',
  qa: 'bg-orange-500',
  done: 'bg-emerald-500',
}

// F-18: the team ring's colour. Red when the last turn was cut short, else by state.
export const ringTone = (t: { state: string; last_turn?: { state: string } | null } | undefined) => {
  if (t?.last_turn && (t.last_turn.state === 'interrupted' || t.last_turn.state === 'failed') && t.state !== 'running') return 'red'
  return t?.state === 'running' ? 'green' : t?.state === 'queued' ? 'amber' : 'gray'
}
// The word under a role's name on the Dashboard: "working" for a plain running turn, the turn kind while a
// handoff / standup / brainstorm runs, "interrupted" / "failed" when the last turn was cut short, else the state.
export const ringWord = (t: { state: string; kind?: string | null; last_turn?: { state: string } | null } | undefined) => {
  if (ringTone(t) === 'red') return t!.last_turn!.state
  const w = stateWord(t)
  return w === 'running' ? 'working' : w
}
export const TONE_TEXT: Record<string, string> = {
  green: 'text-green-700',
  amber: 'text-amber-700',
  gray: 'text-gray-500',
  red: 'text-red-600',
}
export const RING_STROKE: Record<string, string> = {
  green: 'stroke-green-500',
  amber: 'stroke-amber-400',
  gray: 'stroke-gray-300',
  red: 'stroke-red-500',
}

export const STATE_CHIP: Record<string, string> = {
  pending: 'bg-amber-50 text-amber-800',
  approved: 'bg-emerald-50 text-emerald-700',
  changes_requested: 'bg-amber-50 text-amber-800', // F-25: asked to revise, not a no (rejected is the red one)
  lead_review: 'bg-gray-100 text-gray-600',
  master_review: 'bg-amber-50 text-amber-800',
  committed: 'bg-emerald-50 text-emerald-700',
  rejected: 'bg-red-50 text-red-700',
  failed: 'bg-red-50 text-red-700',
  withdrawn: 'bg-gray-100 text-gray-600', // F-34: pulled back by its sender or the feature's Lead; not a no, so not red
}
export const stateChip = (s: string) => STATE_CHIP[s] || 'bg-gray-100 text-gray-600'
export const stateLabel = (s: string) => s.replace(/_/g, ' ')

// F-40: an approval's type, from its title's prefix ("Design: Export orders" -> Design). `rest` is the title without the prefix.
// Anything else is a plain "Approval".
export const APPROVAL_TYPES: Record<string, string> = {
  Design: 'bg-violet-100 text-violet-800',
  'Scope lock': 'bg-blue-100 text-blue-800',
  'Use skill': 'bg-teal-100 text-teal-800',
  Artifact: 'bg-orange-100 text-orange-800',
  Done: 'bg-emerald-100 text-emerald-800',
  Branch: 'bg-indigo-100 text-indigo-800',
  PR: 'bg-sky-100 text-sky-800',
}
const APPROVAL_PREFIX = new RegExp(`^(${Object.keys(APPROVAL_TYPES).join('|')})\\s*:\\s*(.*)$`, 'i')
export function approvalType(title: string): { label: string; chip: string; rest: string } {
  const m = APPROVAL_PREFIX.exec(title.trim())
  const label = m ? Object.keys(APPROVAL_TYPES).find((k) => k.toLowerCase() === m[1].toLowerCase())! : 'Approval'
  return { label, chip: APPROVAL_TYPES[label] ?? 'bg-gray-100 text-gray-700', rest: m ? m[2] : title.trim() }
}
