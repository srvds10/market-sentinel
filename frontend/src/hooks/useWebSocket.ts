import { useCallback, useEffect, useRef, useState } from 'react'
import type { WSMessage } from '../types'

type Handler = (msg: WSMessage) => void

export function useWebSocket(url: string, onMessage: Handler) {
  const ws = useRef<WebSocket | null>(null)
  const [connected, setConnected] = useState(false)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()

  // Always keep the handler current without recreating `connect`.
  // This breaks the onMessage → connect → useEffect dependency chain
  // that was causing the socket to reconnect on every React re-render.
  const handlerRef = useRef(onMessage)
  useEffect(() => { handlerRef.current = onMessage }, [onMessage])

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return

    const socket = new WebSocket(url)
    ws.current = socket

    socket.onopen  = () => setConnected(true)
    socket.onclose = () => {
      setConnected(false)
      reconnectTimer.current = setTimeout(connect, 3000)
    }
    socket.onerror  = () => socket.close()
    socket.onmessage = (e) => {
      try { handlerRef.current(JSON.parse(e.data) as WSMessage) }
      catch { /* malformed frame — ignore */ }
    }
  }, [url])   // ← no longer depends on onMessage

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      ws.current?.close()
    }
  }, [connect])

  return connected
}
