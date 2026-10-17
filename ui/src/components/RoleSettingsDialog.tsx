import { useEffect, useState } from 'react'
import type { SettingsOptions } from '../api'
import { api } from '../api'
import { roleName } from '../roles'
import { useAppDispatch } from '../store'
import { refreshTeam } from '../store/thunks'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'

// Radix Select items can't have an empty value, so "inherit" (role rows) / "none" (Default effort) is a sentinel.
const INHERIT = '__default'

type Row = { model: string; effort: string }
type Draft = { def: Row; roles: Record<string, Row> }

// F-9: global model + effort per role, saved with one PUT /api/settings; applies from each role's next turn.
export default function RoleSettingsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const dispatch = useAppDispatch()
  const [options, setOptions] = useState<SettingsOptions | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Reload from the server every time the dialog opens, so Cancel simply drops the draft.
  useEffect(() => {
    if (!open) return
    setDraft(null)
    setErr(null)
    Promise.all([api.settingsOptions(), api.settings()])
      .then(([o, s]) => {
        setOptions(o)
        setDraft({
          def: { model: s.default_model || o.models[0]?.id,effort: s.default_effort || INHERIT },
          roles: Object.fromEntries(o.roles.map((r) => [r, { model: s.models?.[r] || INHERIT, effort: s.efforts?.[r] || INHERIT }])),
        })
      })
      .catch((e) => setErr(e?.message || 'could not load settings'))
  }, [open])

  const supported = (model: string, level: string) => {
    const list = options?.effort_support?.[model]
    return !list || list.includes(level) // a model missing from the table accepts every level
  }

  // A saved ID that is no longer offered still runs (model_for passes it through), so show it raw.
  const labelOf = (id: string) => options?.models.find((m) => m.id === id)?.label ?? id

  const setRow = (role: string | null, patch: Partial<Row>) =>
    setDraft((d) => {
      if (!d) return d
      const row = { ...(role ? d.roles[role] : d.def), ...patch }
      // A model change can make the chosen level unsupported; fall back to inherit rather than keep a dead value.
      const model = role && row.model === INHERIT ? d.def.model : row.model
      if (patch.model && row.effort !== INHERIT && !supported(model, row.effort)) row.effort = INHERIT
      if (role) return { ...d, roles: { ...d.roles, [role]: row } }
      // A new Default model also applies to every row that inherits it.
      const roles = !patch.model
        ? d.roles
        : Object.fromEntries(
            Object.entries(d.roles).map(([r, x]) =>
              [r, x.model === INHERIT && x.effort !== INHERIT && !supported(row.model, x.effort) ? { ...x, effort: INHERIT } : x],
            ),
          )
      return { def: row, roles }
    })

  const modelItems = (current: string) => {
    const offered = options?.models ?? []
    const extra = current !== INHERIT && !offered.some((m) => m.id === current) ? [{ id: current, label: current }] : []
    return [...offered, ...extra].map((m) => (
      <SelectItem key={m.id} value={m.id}>
        {m.label}
      </SelectItem>
    ))
  }

  const save = async () => {
    if (!draft) return
    setBusy(true)
    setErr(null)
    try {
      const pick = (k: 'model' | 'effort') =>
        Object.fromEntries(Object.entries(draft.roles).flatMap(([r, row]) => (row[k] === INHERIT ? [] : [[r, row[k]]])))
      await api.saveSettings({
        default_model: draft.def.model,
        default_effort: draft.def.effort === INHERIT ? null : draft.def.effort,
        models: pick('model'),
        efforts: pick('effort'),
      })
      dispatch(refreshTeam()) // the pill tooltips show the new effective values without waiting for the poll
      onOpenChange(false)
    } catch (e: any) {
      setErr(e?.message || 'save failed')
    } finally {
      setBusy(false)
    }
  }

  const effortSelect = (label: string, row: Row, model: string, onChange: (v: string) => void, inheritLabel: string) => (
    <Select value={row.effort} onValueChange={onChange}>
      <SelectTrigger size="sm" aria-label={label} className="w-36">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={INHERIT}>{inheritLabel}</SelectItem>
        {options?.efforts.map((l) => {
          const ok = supported(model, l)
          return (
            <SelectItem key={l} value={l} disabled={!ok}>
              {ok ? l : `${l} (not supported by ${labelOf(model)})`}
            </SelectItem>
          )
        })}
      </SelectContent>
    </Select>
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl" data-role-settings>
        <DialogHeader>
          <DialogTitle>Role settings</DialogTitle>
          <DialogDescription>Global for all projects. Applies from each role's next turn.</DialogDescription>
        </DialogHeader>
        {!draft || !options ? (
          <div className="text-meta text-gray-400">{err ? '' : 'Loading…'}</div>
        ) : (
          <table className="w-full text-meta">
            <thead>
              <tr className="text-caption text-gray-500 text-left">
                <th className="font-medium py-1">Role</th>
                <th className="font-medium py-1">Model</th>
                <th className="font-medium py-1">Effort</th>
              </tr>
            </thead>
            <tbody>
              <tr data-settings-row="default">
                <td className="py-1 font-semibold">Default</td>
                <td className="py-1">
                  <Select value={draft.def.model} onValueChange={(v) => setRow(null, { model: v })}>
                    <SelectTrigger size="sm" aria-label="Default model" className="w-52">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>{modelItems(draft.def.model)}</SelectContent>
                  </Select>
                </td>
                <td className="py-1">{effortSelect('Default effort', draft.def, draft.def.model, (v) => setRow(null, { effort: v }), 'none')}</td>
              </tr>
              {options.roles.map((r) => {
                const row = draft.roles[r]
                const model = row.model === INHERIT ? draft.def.model : row.model
                return (
                  <tr key={r} data-settings-row={r}>
                    <td className="py-1">{roleName(r)}</td>
                    <td className="py-1">
                      <Select value={row.model} onValueChange={(v) => setRow(r, { model: v })}>
                        <SelectTrigger size="sm" aria-label={`${roleName(r)} model`} className="w-52">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value={INHERIT}>Default ({labelOf(draft.def.model)})</SelectItem>
                          {modelItems(row.model)}
                        </SelectContent>
                      </Select>
                    </td>
                    <td className="py-1">
                      {effortSelect(`${roleName(r)} effort`, row, model, (v) => setRow(r, { effort: v }), `Default (${draft.def.effort === INHERIT ? 'none' : draft.def.effort})`)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        {err && <div className="text-caption text-red-600 break-words">{err}</div>}
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={!draft || busy} onClick={save}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
