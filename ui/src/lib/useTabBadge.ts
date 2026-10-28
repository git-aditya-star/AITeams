import { useEffect } from 'react'

// F-35: the browser tab says when the Inbox needs the Master: "(N) AITeams" and the icon with a red dot, back to
// "AITeams" and the plain icon at zero. The files are under /assets because that is the only static path the daemon serves.
const TITLE = 'AITeams'
const ICON = '/assets/favicon.svg'
const ICON_DOT = '/assets/favicon-dot.svg'

export function useTabBadge(count: number) {
  useEffect(() => {
    document.title = count > 0 ? `(${count}) ${TITLE}` : TITLE
    // [rel="icon"] is an exact match: it leaves the `alternate icon` PNG fallback alone.
    document.querySelector('link[rel="icon"]')?.setAttribute('href', count > 0 ? ICON_DOT : ICON)
  }, [count])
}
