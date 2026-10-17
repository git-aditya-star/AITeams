import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, type Health } from './api'

export default function Home() {
  const nav = useNavigate()
  const [state, setState] = useState<'loading' | 'empty' | 'error'>('loading')
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    api.health().then(setHealth).catch(() => {})
    api
      .projects()
      .then((ps) => (ps.length ? nav(`/p/${ps[0].id}`, { replace: true }) : setState('empty')))
      .catch(() => setState('error'))
  }, [nav])

  const upgradeAvailable = !!health?.source_head && !!health?.sha && health.source_head !== health.sha
  return (
    <div className="h-full flex flex-col">
      {upgradeAvailable && (
        <div className="bg-gray-50 border-b border-gray-200 text-gray-700 px-4 py-1.5 text-meta shrink-0">
          New commits since the running version, run <code className="font-mono text-code bg-gray-100 rounded-sm px-1">aiteams upgrade</code>
        </div>
      )}
    <div className="flex-1 flex items-center justify-center text-gray-600">
      {state === 'loading' && <span>Loading…</span>}
      {state === 'empty' && (
        <div className="text-center">
          <div className="text-title text-gray-800 mb-1">No projects yet</div>
          <div>
            Run <code className="font-mono text-code bg-gray-100 rounded-sm px-1">aiteams</code> in a git folder to register one.
          </div>
        </div>
      )}
      {state === 'error' && <span>Could not reach the daemon.</span>}
    </div>
    </div>
  )
}
