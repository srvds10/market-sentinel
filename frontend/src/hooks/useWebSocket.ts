import { useCallback, useEffect, useRef, useState } from 'react'
import type { WSMessage } from '../types'

type Handler = (msg: WSMessage) => void

export function useWebSocket(url: string, onMessage: Handler) {
  const ws = useRef<WebSocket | null>(null)
  const [connected, setConnected] = useState(false)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return

    const socket = new WebSocket(url)
    ws.current = socket

    socket.onopen = () => setConnected(true)
    socket.onclose = () => {
      setConnected(false)
      reconnectTimer.current = setTimeout(connect, 3000)
    }
    socket.onerror = () => socket.close()
    socket.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data) as WSMessage)
      } catch { /* malformed frame — ignore */ }
    }
  }, [url, onMessage])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      ws.current?.close()
    }
  }, [connect])

  return connected
}
