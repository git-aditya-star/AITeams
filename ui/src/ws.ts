import { useEffect, useRef } from 'react'

export interface WsFrame {
  event: string
  data: any
}

// Opens /ws/p/{pid}; reconnects with 1s→10s backoff. onReconnect fires on every
// successful open after the first, so callers can refetch state they may have missed.
export function useProjectWs(
  pid: string | undefined,
  onFrame: (f: WsFrame) => void,
  onReconnect: () => void,
) {
  const frameRef = useRef(onFrame)
  const reconnRef = useRef(onReconnect)
  frameRef.current = onFrame
  reconnRef.current = onReconnect

  useEffect(() => {
    if (!pid) return
    let ws: WebSocket | null = null
    let timer: number | undefined
    let backoff = 1000
    let opened = 0
    let closed = false

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/p/${pid}`)
      ws.onopen = () => {
        backoff = 1000
        opened += 1
        if (opened > 1) reconnRef.current()
      }
      ws.onmessage = (ev) => {
        try {
          frameRef.current(JSON.parse(ev.data))
        } catch {
          /* ignore malformed frame */
        }
      }
      ws.onclose = () => {
        if (closed) return
        timer = window.setTimeout(connect, backoff)
        backoff = Math.min(backoff * 2, 10000)
      }
      ws.onerror = () => ws?.close()
    }
    connect()
    return () => {
      closed = true
      if (timer) clearTimeout(timer)
      ws?.close()
    }
  }, [pid])
}
