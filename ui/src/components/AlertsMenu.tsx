import { Bell, BellOff } from 'lucide-react'
import { setAlertSetting, useAlertSetting, useIsAlerting, usePopupPermission } from '../alerts'
import { useAudioStatus } from '../alertSound'
import { Button } from './ui/button'
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover'
import { Switch } from './ui/switch'

// F-28: the Alerts button in the sidebar footer and its popover with the two switches. Both are on by default and are
// remembered in this browser. `data-alerting` says whether this tab is the one that plays and pops up (the Web Lock).
export default function AlertsMenu() {
  const sound = useAlertSetting('sound')
  const popup = useAlertSetting('popup')
  const alerting = useIsAlerting()
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="xs" data-alerts-trigger data-alerting={alerting} className="text-gray-600">
          {sound || popup ? <Bell /> : <BellOff />}
          Alerts
        </Button>
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-64" data-alerts-panel>
        <div className="mb-2 font-semibold text-gray-800">Alerts</div>
        <div className="flex flex-col gap-2.5">
          <div className="flex items-center justify-between gap-3">
            <label htmlFor="alert-sound" className="cursor-pointer text-gray-700">
              Sound
            </label>
            <Switch id="alert-sound" size="sm" data-alert-switch="sound" checked={sound} onCheckedChange={(v) => setAlertSetting('sound', v)} />
          </div>
          <div className="flex items-center justify-between gap-3">
            <label htmlFor="alert-popup" className="cursor-pointer text-gray-700">
              Pop-up
            </label>
            <Switch id="alert-popup" size="sm" data-alert-switch="popup" checked={popup} onCheckedChange={(v) => setAlertSetting('popup', v)} />
          </div>
        </div>
        <p className="mt-3 text-caption text-gray-500">
          For anything that needs you: a sign-off (approvals, commit requests), an answer (questions, cards, blockers) or a message to @master. Each kind has its own tone.
        </p>
      </PopoverContent>
    </Popover>
  )
}

// Under the footer row, outside the popover, so a blocked channel is seen without opening it (and the first click, which
// unblocks sound, does not hide the line before it was read).
export function AlertHints() {
  const sound = useAlertSetting('sound')
  const popup = useAlertSetting('popup')
  const audio = useAudioStatus()
  const permission = usePopupPermission()
  const soundBlocked = sound && audio === 'suspended'
  const popupBlocked = popup && (permission === 'denied' || permission === 'unsupported')
  if (!soundBlocked && !popupBlocked) return null
  return (
    <div className="flex flex-col gap-0.5 px-4 pb-2 text-caption text-amber-700">
      {soundBlocked && <div data-alert-hint="sound">Click anywhere to enable sound</div>}
      {popupBlocked && <div data-alert-hint="popup">{permission === 'denied' ? 'Pop-ups are blocked by the browser' : 'Pop-ups are not available in this browser'}</div>}
    </div>
  )
}
