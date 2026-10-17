import type { TeamRole } from '../api'
import { ROLES, dotClass, roleName } from '../roles'

export default function TeamStrip({ team, onSelect }: { team: TeamRole[]; onSelect: (role: string) => void }) {
  const byRole = Object.fromEntries(team.map((t) => [t.role, t]))
  return (
    <div className="flex flex-wrap gap-1.5 px-4 py-1.5 border-b border-gray-200 bg-white shrink-0">
      {ROLES.map((r) => {
        const t = byRole[r]
        return (
          <button
            key={r}
            onClick={() => onSelect(r)}
            title={t ? `${t.model} · ${t.state}` : r}
            className="flex items-center gap-1.5 rounded-full border border-gray-200 px-2 py-0.5 text-[11.5px] text-gray-600 hover:bg-gray-50"
          >
            <span className={`w-2 h-2 rounded-full ${dotClass(t?.state || 'idle')}`} />
            <span className="font-medium text-gray-700">{roleName(r)}</span>
            {t && (
              <>
                <span>{Math.round((t.context_pct || 0) * 100)}%</span>
                <span className="text-gray-400">${(t.cost_today || 0).toFixed(2)}</span>
              </>
            )}
          </button>
        )
      })}
    </div>
  )
}
