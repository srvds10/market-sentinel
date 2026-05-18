import { useEffect, useState } from 'react'
import type { Trade, Signal } from '../types'

async function fetcher<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export function useTrades(limit = 50) {
  const [trades, setTrades] = useState<Trade[]>([])

  const refresh = async () => {
    try {
      setTrades(await fetcher<Trade[]>(`/api/trades?limit=${limit}`))
    } catch { /* swallow on stale fetch */ }
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 10_000)
    return () => clearInterval(id)
  }, [limit])

  return { trades, refresh }
}

export function useSignals(limit = 20) {
  const [signals, setSignals] = useState<Signal[]>([])

  const refresh = async () => {
    try {
      setSignals(await fetcher<Signal[]>(`/api/signals?limit=${limit}`))
    } catch {}
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 5_000)
    return () => clearInterval(id)
  }, [limit])

  return { signals, refresh }
}

export async function patchConfig(section: string, key: string, value: unknown) {
  const res = await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ section, key, value }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchConfig() {
  return fetcher<Record<string, Record<string, unknown>>>('/api/config')
}

export async function updateToken(access_token: string) {
  const res = await fetch('/api/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ access_token }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function downloadLogs() {
  const res = await fetch('/api/logs/download')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'market-sentinel.log'
  a.click()
  URL.revokeObjectURL(url)
}

export async function clearLogs() {
  const res = await fetch('/api/logs/clear', { method: 'DELETE' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchLogSize(): Promise<{ bytes: number; exists: boolean }> {
  const res = await fetch('/api/logs/size')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function clearRecords(opts: { trades?: boolean; signals?: boolean; logs?: boolean } = {}) {
  const params = new URLSearchParams()
  if (opts.trades  !== undefined) params.set('trades',  String(opts.trades))
  if (opts.signals !== undefined) params.set('signals', String(opts.signals))
  if (opts.logs    !== undefined) params.set('logs',    String(opts.logs))
  const url = `/api/records/clear${params.toString() ? '?' + params : ''}`
  const res = await fetch(url, { method: 'DELETE' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<{ cleared: boolean; deleted: Record<string, number> }>
}
