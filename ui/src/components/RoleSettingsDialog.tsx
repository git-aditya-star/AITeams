import { Plus, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { PluginStatus, SettingsOptions } from '../api'
import { api, fmtTime } from '../api'
import { roleName } from '../roles'
import { useAppDispatch } from '../store'
import { refreshTeam } from '../store/thunks'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './ui/dialog'
import { Input } from './ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select'
import { Switch } from './ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs'
import { Tip } from './ui/tooltip'

// Radix Select items can't have an empty value, so "inherit" (role rows) / "none" (Default effort) is a sentinel.
const INHERIT = '__default'

type Row = { model: string; effort: string }
// F-23: `key` keeps a plugin row's identity while its id is being typed; `plugins` is null when the daemon predates
// the endpoint (the tab says so and Save leaves the list alone).
type PluginDraft = { key: number; id: string; on: boolean; ask: boolean }
type Draft = { def: Row; roles: Record<string, Row>; plugins: PluginDraft[] | null; status: Record<string, PluginStatus> }

// F-9: global model + effort per role, saved with one PUT /api/settings; applies from each role's next turn.
// F-23: the Plugins tab edits the one plugin list in the same draft and the same PUT.
export default function RoleSettingsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const dispatch = useAppDispatch()
  const [options, setOptions] = useState<SettingsOptions | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('roles')
  const keys = useRef(0)
  const [added, setAdded] = useState<number | null>(null) // the row to focus after Add plugin

  // Reload from the server every time the dialog opens, so Cancel simply drops the draft.
  useEffect(() => {
    if (!open) return
    setDraft(null)
    setErr(null)
    setTab('roles')
    setAdded(null)
    Promise.all([api.settingsOptions(), api.settings(), api.pluginSettings().catch(() => null)])
      .then(([o, s, p]) => {
        setOptions(o)
        setDraft({
          def: { model: s.default_model || o.models[0]?.id,effort: s.default_effort || INHERIT },
          roles: Object.fromEntries(o.roles.map((r) => [r, { model: s.models?.[r] || INHERIT, effort: s.efforts?.[r] || INHERIT }])),
          plugins: p ? p.rows.map((r) => ({ key: ++keys.current, ...r })) : null,
          status: p?.status ?? {},
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
      return { ...d, def: row, roles }
    })

  const setPlugins = (f: (rows: PluginDraft[]) => PluginDraft[]) => setDraft((d) => (d && d.plugins ? { ...d, plugins: f(d.plugins) } : d))
  const setPlugin = (key: number, patch: Partial<PluginDraft>) => setPlugins((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const addPlugin = () => {
    const key = ++keys.current
    setAdded(key)
    setPlugins((rows) => [...rows, { key, id: '', on: true, ask: false }])
  }

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
        // A blank row is an unfinished one: drop it rather than send an id the server would refuse.
        ...(draft.plugins ? { plugins: draft.plugins.filter((r) => r.id.trim()).map((r) => ({ id: r.id.trim(), on: r.on, ask: r.ask })) } : {}),
      })
      dispatch(refreshTeam()) // the pill tooltips show the new effective values without waiting for the poll
      onOpenChange(false)
    } catch (e: any) {
      setErr(typeof e?.detail === 'string' ? e.detail : e?.message || 'save failed') // the server's 400 sentence, not the raw request line
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

  const pluginsTab = () => {
    const rows = draft?.plugins
    if (!rows) return <div className="text-meta text-gray-400">Not available from this daemon; run aiteams upgrade.</div>
    const status = draft!.status
    return (
      <div className="flex flex-col gap-3">
        <p className="text-meta text-gray-500">
          One list for all roles. A listed plugin is switched on or off for every turn; Ask me first makes a role get your approval before it uses one of that
          plugin's skills. Plugins you don't list keep Claude Code's default.
        </p>
        {rows.length === 0 ? (
          <div data-plugins-empty className="rounded-md border border-dashed border-gray-300 px-4 py-6 text-center text-meta text-gray-500">
            <div className="text-gray-700">No plugins listed. Claude Code's defaults apply.</div>
            <div className="mt-1">
              Add <span className="font-mono text-code text-gray-700">project-notes@project-notes</span> to give roles their notebook.
            </div>
          </div>
        ) : (
          <div className="grid max-h-[50vh] grid-cols-[minmax(0,1fr)_2.5rem_5.5rem_1.75rem] items-center gap-x-3 gap-y-2 overflow-y-auto">
            <span className="text-caption font-medium text-gray-500">Plugin id</span>
            <span className="text-center text-caption font-medium text-gray-500">On</span>
            <span className="text-center text-caption font-medium text-gray-500">Ask me first</span>
            <span />
            {rows.map((r) => {
              const st = status[r.id.trim()]
              return (
                <div key={r.key} data-plugin-row={r.id} className="contents">
                  <div className="relative min-w-0">
                    <Input
                      aria-label="Plugin id"
                      value={r.id}
                      placeholder="name@marketplace"
                      spellCheck={false}
                      autoCapitalize="off"
                      autoComplete="off"
                      autoFocus={r.key === added}
                      className={`font-mono text-code ${r.on && st?.missing ? 'pr-20' : ''}`}
                      onChange={(e) => setPlugin(r.key, { id: e.target.value })}
                    />
                    {r.on && st?.missing && (
                      <Tip content={`Not loaded by ${roleName(st.role)}'s turn at ${fmtTime(st.checked_at)}`}>
                        <span className="absolute right-1.5 top-1/2 -translate-y-1/2">
                          <Badge data-plugin-missing className="bg-red-50 px-2 text-red-700">
                            missing
                          </Badge>
                        </span>
                      </Tip>
                    )}
                  </div>
                  <Switch aria-label="On" size="sm" checked={r.on} onCheckedChange={(v) => setPlugin(r.key, { on: v })} className="justify-self-center" />
                  <Switch aria-label="Ask me first" size="sm" checked={r.ask} onCheckedChange={(v) => setPlugin(r.key, { ask: v })} className="justify-self-center" />
                  <Tip content="Remove">
                    <Button variant="ghost" size="icon-xs" aria-label="Remove plugin" className="text-gray-400 hover:text-red-600" onClick={() => setPlugins((x) => x.filter((y) => y.key !== r.key))}>
                      <Trash2 className="size-4" />
                    </Button>
                  </Tip>
                </div>
              )
            })}
          </div>
        )}
        <Button variant="outline" size="sm" className="self-start" onClick={addPlugin}>
          <Plus />
          Add plugin
        </Button>
      </div>
    )
  }

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
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="roles">Roles</TabsTrigger>
              <TabsTrigger value="plugins">Plugins</TabsTrigger>
            </TabsList>
            <TabsContent value="roles">
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
            </TabsContent>
            <TabsContent value="plugins" data-plugins>
              {pluginsTab()}
            </TabsContent>
          </Tabs>
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
