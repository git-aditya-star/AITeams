import { useState } from 'react'
import type { Conversation, Project, ProjectSummary, TeamRole } from '../api'
import { ROLES, dotClass, meterClass, roleName } from '../roles'

export default function Sidebar({
  projects,
  project,
  conversations,
  team,
  current,
  paused,
  onPickProject,
  onSelect,
  onNewConversation,
  onPause,
}: {
  projects: ProjectSummary[]
  project: Project | null
  conversations: Conversation[]
  team: TeamRole[]
  current: string
  paused: boolean
  onPickProject: (id: string) => void
  onSelect: (id: string) => void
  onNewConversation: (title: string) => Promise<void>
  onPause: (paused: boolean) => void
}) {
  const [creating, setCreating] = useState(false)
  const [title, setTitle] = useState('')
  const byRole = Object.fromEntries(team.map((t) => [t.role, t]))
  const threads = conversations
    .filter((c) => c.kind === 'thread')
    .sort((a, b) => (b.last_message_at ?? b.created_at) - (a.last_message_at ?? a.created_at))

  const row = (id: string, label: React.ReactNode, extra?: React.ReactNode) => (
    <button
      key={id}
      onClick={() => onSelect(id)}
      className={`w-full text-left px-3 py-1 rounded-md text-[13.5px] ${current === id ? 'bg-blue-100 text-blue-900 font-medium' : 'text-gray-700 hover:bg-gray-200/60'}`}
    >
      {label}
      {extra}
    </button>
  )

  const submitNew = async () => {
    const t = title.trim()
    if (!t) return
    await onNewConversation(t)
    setTitle('')
    setCreating(false)
  }

  return (
    <aside className="w-[260px] shrink-0 bg-gray-50 border-r border-gray-200 flex flex-col h-full">
      <div className="p-3 border-b border-gray-200">
        <select
          value={project?.id || ''}
          onChange={(e) => onPickProject(e.target.value)}
          className="w-full rounded-md border border-gray-300 bg-white px-2 py-1.5 text-[13.5px] font-medium"
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {project && !project.is_git && <div className="mt-1 text-[11.5px] text-amber-700">not a git repo</div>}
        {creating ? (
          <div className="mt-2 flex gap-1">
            <input
              autoFocus
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') void submitNew(); if (e.key === 'Escape') setCreating(false) }}
              placeholder="Conversation title"
              className="flex-1 min-w-0 rounded-md border border-gray-300 px-2 py-1 text-[13px]"
            />
            <button onClick={submitNew} className="rounded-md bg-blue-600 text-white px-2 text-xs font-medium hover:bg-blue-700">OK</button>
          </div>
        ) : (
          <button onClick={() => setCreating(true)} className="mt-2 w-full rounded-md bg-blue-600 text-white py-1.5 text-[13px] font-medium hover:bg-blue-700">
            New conversation
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-3">
        <div>
          <div className="px-3 text-[11px] uppercase tracking-wide text-gray-500 font-semibold mb-1">Channels</div>
          {row('general', '# general')}
        </div>
        <div>
          <div className="px-3 text-[11px] uppercase tracking-wide text-gray-500 font-semibold mb-1">Conversations</div>
          {threads.length === 0 && <div className="px-3 text-xs text-gray-400">none yet</div>}
          {threads.map((c) => row(c.id, <span className="block truncate">{c.title}</span>))}
        </div>
        <div>
          <div className="px-3 text-[11px] uppercase tracking-wide text-gray-500 font-semibold mb-1">Direct messages</div>
          {ROLES.map((r) => {
            const t = byRole[r]
            const pct = Math.min(1, Math.max(0, t?.context_pct || 0))
            return row(
              `dm-${r}`,
              <span className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass(t?.state || 'idle')}`} />
                <span>{roleName(r)}</span>
              </span>,
              <span className="block ml-4 mt-1 h-[3px] rounded bg-gray-200 overflow-hidden" title={t ? `context ${Math.round(pct * 100)}%` : undefined}>
                <span className={`block h-full ${meterClass(pct)}`} style={{ width: `${pct * 100}%` }} />
              </span>,
            )
          })}
        </div>
      </div>

      <label className="flex items-center gap-2 px-4 py-3 border-t border-gray-200 text-[13px] text-gray-700 cursor-pointer select-none">
        <input type="checkbox" checked={paused} onChange={(e) => onPause(e.target.checked)} />
        Pause project
      </label>
    </aside>
  )
}
