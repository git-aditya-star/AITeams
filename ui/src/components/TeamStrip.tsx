import { Settings } from 'lucide-react'
import { useState } from 'react'
import { ROLES, contextNote, contextPct, dotClass, roleName, stateWord } from '../roles'
import { useAppSelector } from '../store'
import { selectTeamByRole } from '../store/selectors'
import RoleSettingsDialog from './RoleSettingsDialog'
import { Button } from './ui/button'
import { Tip } from './ui/tooltip'

export default function TeamStrip({ onSelect }: { onSelect: (role: string) => void }) {
  const byRole = useAppSelector(selectTeamByRole)
  const [settingsOpen, setSettingsOpen] = useState(false)
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-4 py-1.5 border-b border-gray-200 bg-white shrink-0">
      {ROLES.map((r) => {
        const t = byRole[r]
        return (
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
