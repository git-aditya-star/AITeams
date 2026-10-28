import { Settings } from 'lucide-react'
import { useState } from 'react'
import { ROLES, contextNote, contextPct, dotClass, laneLabel, roleName, stateWord } from '../roles'
import { useAppSelector } from '../store'
import { selectTeamByRole } from '../store/selectors'
import RoleSettingsDialog from './RoleSettingsDialog'
import { Button } from './ui/button'
import { Tip } from './ui/tooltip'

// F-41: `onSelectLane` opens that session's Live view (a role's chip opens its DM, as before).
export default function TeamStrip({ onSelect, onSelectLane }: { onSelect: (role: string) => void; onSelectLane: (role: string, lane: string) => void }) {
  const byRole = useAppSelector(selectTeamByRole)
  const [settingsOpen, setSettingsOpen] = useState(false)
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-4 py-1.5 border-b border-gray-200 bg-white shrink-0">
      {ROLES.flatMap((r) => {
        const t = byRole[r]
        const chip = (
          <Tip
            key={r}
            content={
              t
                ? `${t.model_label || t.model} · ${t.effort ? `${t.effort} effort` : 'default effort'} · ${stateWord(t)}${t.stuck ? ' · stuck: running or queued for a long time, not stopped' : ''}${contextNote(t) ? ` · context full, ${contextNote(t)}` : ''}`
                : r
            }
          >
            <button
              onClick={() => onSelect(r)}
              className="flex cursor-pointer items-center gap-1.5 rounded-full border border-gray-200 px-2 py-0.5 text-caption text-gray-600 hover:bg-gray-50"
            >
              <span className={`w-2 h-2 rounded-full ${dotClass(t?.state || 'idle')}`} />
              <span className="font-medium text-gray-700">{roleName(r)}</span>
              {t && (
                <>
                  {t.state !== 'idle' && <span className="text-gray-500">{stateWord(t)}</span>}
                  {t.stuck && (
                    <span data-stuck className="rounded bg-amber-100 px-1 font-semibold text-amber-800">
                      stuck
                    </span>
                  )}
                  <span>{Math.round(contextPct(t) * 100)}%</span>
                </>
              )}
            </button>
          </Tip>
        )
        // F-41: one small chip per active feature session of this role, with its own state dot and context meter
        const lanes = (t?.lanes ?? []).map((l) => (
          <Tip
            key={`${r}:${l.lane}`}
            content={`${laneLabel(r, l.lane)} · ${stateWord(l)}${l.stuck ? ' · stuck: running or queued for a long time, not stopped' : ''}${contextNote(l) ? ` · context full, ${contextNote(l)}` : ''}`}
          >
            <button
              data-lane-chip={`${r}:${l.lane}`}
              aria-label={`${l.lane} · ${roleName(r)} session ${Math.round(contextPct(l) * 100)}%`}
              onClick={() => onSelectLane(r, l.lane)}
              className="flex cursor-pointer items-center gap-1.5 rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-caption text-blue-800 hover:bg-blue-100"
            >
              <span className={`w-2 h-2 rounded-full ${dotClass(l.state || 'idle')}`} />
              <span className="font-medium">{l.lane}</span>
              {l.stuck && (
                <span data-stuck className="rounded bg-amber-100 px-1 font-semibold text-amber-800">
                  stuck
                </span>
              )}
              <span className="text-blue-600">{Math.round(contextPct(l) * 100)}%</span>
            </button>
          </Tip>
        ))
        return [chip, ...lanes]
      })}
      <Tip content="Role settings">
        <Button variant="ghost" size="icon-xs" className="ml-auto text-gray-500" aria-label="Role settings" onClick={() => setSettingsOpen(true)}>
          <Settings className="size-4" />
        </Button>
      </Tip>
      <RoleSettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  )
}
