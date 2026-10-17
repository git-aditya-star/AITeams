import { Lightbulb, X } from 'lucide-react'
import { useState } from 'react'
import type { Feature } from '../api'
import BrainstormDialog from './BrainstormDialog'
import { PHASES, ROLES, phaseChip, roleName } from '../roles'
import { useAppDispatch, useAppSelector } from '../store'
import { freshStartFeature, patchFeature, releaseLock } from '../store/thunks'
import { Button } from './ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { Tabs, TabsList, TabsTrigger } from './ui/tabs'
import { Tip } from './ui/tooltip'

export type FeatureTab = 'chat' | 'docs'

const NO_OWNER = 'none' // Radix Select items cannot have an empty value

export default function FeatureHeader({ feature, tab, onTab }: { feature: Feature; tab: FeatureTab; onTab: (t: FeatureTab) => void }) {
  const dispatch = useAppDispatch()
  const locks = useAppSelector((s) => s.locks)
  const error = useAppSelector((s) => s.session.error)
  const onPatch = (patch: { phase?: string; owner?: string | null; archived?: boolean }) => dispatch(patchFeature(feature.id, patch))
  const onReleaseLock = (path: string) => dispatch(releaseLock(path))
  const onFreshStart = (mode: 'keep_docs' | 'start_over') => dispatch(freshStartFeature(feature, mode))
  const [busy, setBusy] = useState(false)
  const [fresh, setFresh] = useState<null | 'choose' | 'confirm'>(null)
  const [brainstorm, setBrainstorm] = useState(false)
  const archived = !!feature.archived
  const run = (p: Promise<void>) => {
    setBusy(true)
    p.catch(() => {}).finally(() => setBusy(false))
  }
  const freshStart = (mode: 'keep_docs' | 'start_over') => run(onFreshStart(mode).then(() => setFresh(null)))

  return (
    <div className="border-b border-gray-200 shrink-0">
      <div className="px-4 py-2 flex items-center gap-3 flex-wrap">
        <span className="font-mono text-caption text-gray-500">{feature.key}</span>
        <h1 className="text-title truncate max-w-[40%]">{feature.title}</h1>
        <label className="flex items-center gap-1 text-caption text-gray-500">
          phase
          <Select value={feature.phase} disabled={busy} onValueChange={(v) => run(onPatch({ phase: v }))}>
            <SelectTrigger size="xs" aria-label="Phase" className={`border-transparent ${phaseChip(feature.phase)}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PHASES.map((p) => (
                <SelectItem key={p} value={p}>
                  {p}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="flex items-center gap-1 text-caption text-gray-500">
          owner
          <Select value={feature.owner || NO_OWNER} disabled={busy} onValueChange={(v) => run(onPatch({ owner: v === NO_OWNER ? null : v }))}>
            <SelectTrigger size="xs" aria-label="Owner">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NO_OWNER}>none</SelectItem>
              {ROLES.map((r) => (
                <SelectItem key={r} value={r}>
                  {roleName(r)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <Button variant="outline" size="xs" disabled={busy} onClick={() => run(onPatch({ archived: !archived }))}>
          {archived ? 'Unarchive' : 'Archive'}
        </Button>
        <Button variant="outline" size="xs" disabled={busy} onClick={() => setFresh(fresh ? null : 'choose')}>
          Fresh start
        </Button>
        <Button variant="outline" size="xs" onClick={() => setBrainstorm(true)}>
          <Lightbulb />
          Brainstorm
        </Button>
        <BrainstormDialog feature={feature} open={brainstorm} onOpenChange={setBrainstorm} />
        <Tabs value={tab} onValueChange={(v) => onTab(v as FeatureTab)} className="ml-auto">
          <TabsList>
            <TabsTrigger value="chat">Chat</TabsTrigger>
            <TabsTrigger value="docs">Docs</TabsTrigger>
          </TabsList>
        </Tabs>
        {error && <span className="text-caption text-red-600 truncate">{error}</span>}
      </div>
      {fresh && (
        <div className="px-4 pb-2 flex items-center gap-3 text-caption flex-wrap">
          {fresh === 'choose' ? (
            <>
              <Button variant="outline" size="xs" disabled={busy} onClick={() => freshStart('keep_docs')}>
                Keep docs
              </Button>
              <span className="text-gray-500">conversation restarts from here; docs, phase and owner stay</span>
              <Button variant="outline" size="xs" disabled={busy} onClick={() => setFresh('confirm')}>
                Start over
              </Button>
              <span className="text-gray-500">docs archived, back to intake, the original request is re-posted</span>
            </>
          ) : (
            <>
              <span className="text-gray-700">Archive docs and restart?</span>
              <Button variant="destructive" size="xs" disabled={busy} onClick={() => freshStart('start_over')}>
                Yes, start over
              </Button>
            </>
          )}
          <Button variant="ghost" size="xs" onClick={() => setFresh(null)}>
            Cancel
          </Button>
        </div>
      )}
      {locks.length > 0 && (
        <div className="px-4 pb-2 flex items-center gap-1.5 flex-wrap">
          <span className="text-caption text-gray-400">locks</span>
          {locks.map((l) => (
            <Tip key={l.path} content={`turn ${l.turn_id ?? '?'}`}>
              <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 px-2 py-px text-caption text-gray-700">
                <span className="font-mono">{l.path}</span>
                <span className="text-gray-400">·</span>
                <span>{roleName(l.role)}</span>
                <Tip content="force-release">
                  <Button variant="ghost" size="inline" onClick={() => void onReleaseLock(l.path)} aria-label={`force-release ${l.path}`} className="text-gray-400 hover:bg-transparent hover:text-red-600">
                    <X className="size-3" />
                  </Button>
                </Tip>
              </span>
            </Tip>
          ))}
        </div>
      )}
    </div>
  )
}
