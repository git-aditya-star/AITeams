import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from './api'

export default function Home() {
  const nav = useNavigate()
  const [state, setState] = useState<'loading' | 'empty' | 'error'>('loading')

  useEffect(() => {
    api
      .projects()
      .then((ps) => (ps.length ? nav(`/p/${ps[0].id}`, { replace: true }) : setState('empty')))
      .catch(() => setState('error'))
  }, [nav])

  return (
    <div className="h-full flex items-center justify-center text-gray-600">
      {state === 'loading' && <span>Loading…</span>}
      {state === 'empty' && (
        <div className="text-center">
          <div className="font-semibold text-gray-800 mb-1">No projects yet</div>
          <div>
            Run <code className="font-mono bg-gray-100 rounded px-1">aiteams</code> in a git folder to register one.
          </div>
        </div>
      )}
      {state === 'error' && <span>Could not reach the daemon.</span>}
    </div>
  )
}
