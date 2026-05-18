import { useCallback, useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'

import { ZScoreGauge } from './components/ZScoreGauge'
import { MarketBias } from './components/MarketBias'
import { OptionPressure } from './components/OptionPressure'
import { SignalFeed } from './components/SignalFeed'
import { PositionTracker } from './components/PositionTracker'
import { TradeBlotter } from './components/TradeBlotter'
import { ConfigPanel } from './components/ConfigPanel'
import { useWebSocket } from './hooks/useWebSocket'
import { useTrades, useSignals } from './hooks/useApi'
import type { StatusPayload, Signal, WSMessage } from './types'

const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/live`
const Z_THRESHOLD = 2.0

const ENGINE_COLORS: Record<string, string> = {
  IDLE:       'text-muted',
  WARMING_UP: 'text-warn',
  ACTIVE:     'text-bull',
  KILLED:     'text-bear',
  CLOSED:     'text-muted',
}

function fmt(n: number, dec = 2) {
  return n.toLocaleString('en-IN', { maximumFractionDigits: dec })
}

function fmtPct(n: number) {
  return `${n >= 0 ? '+' : ''}${(n * 100).toFixed(2)}%`
}

function useUptime(startedAt: number | undefined) {
  const [uptime, setUptime] = useState('')
  useEffect(() => {
    if (!startedAt) return
    const tick = () => {
      const secs = Math.floor(Date.now() / 1000 - startedAt)
      const h = Math.floor(secs / 3600)
      const m = Math.floor((secs % 3600) / 60)
      const s = secs % 60
      setUptime(`${h}h ${m}m ${s}s`)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [startedAt])
  return uptime
}

export default function App() {
  const [status, setStatus] = useState<Partial<StatusPayload>>({})
  const [priceHistory, setPriceHistory] = useState<Array<{ t: number; spot: number; z: number }>>([])
  const [pnlHistory, setPnlHistory] = useState<Array<{ t: number; pnl: number }>>([])
  const [liveSignals, setLiveSignals] = useState<Signal[]>([])
  const [tab, setTab] = useState<'blotter' | 'config'>('blotter')
  const [serverStartedAt, setServerStartedAt] = useState<number>()
  // Rolling price histories for option-pressure panel (15-min window at 2s heartbeat)
  const [itmCallHist, setItmCallHist] = useState<number[]>([])
  const [atmCallHist, setAtmCallHist] = useState<number[]>([])
  const [otmCallHist, setOtmCallHist] = useState<number[]>([])
  const [otmPutHist,  setOtmPutHist]  = useState<number[]>([])
  const [atmPutHist,  setAtmPutHist]  = useState<number[]>([])
  const [itmPutHist,  setItmPutHist]  = useState<number[]>([])
  // Track server reconnect count so histories are wiped when the instrument grid changes
  const prevReconnectRef = useRef<number>(-1)

  const { trades, refresh: refreshTrades } = useTrades(100)
  const { signals, refresh: refreshSignals } = useSignals(30)
  const uptime = useUptime(serverStartedAt)

  const onMessage = useCallback((msg: WSMessage) => {
    if (msg.type === 'ping') return
    if (msg.type === 'status') {
      setStatus(prev => ({ ...prev, ...msg }))
      if (msg.spot_ltp) {
        const t = Date.now()
        setPriceHistory(prev =>
          [...prev, { t, spot: msg.spot_ltp!, z: msg.last_z_score ?? 0 }].slice(-900)
        )
        setPnlHistory(prev =>
          [...prev, { t, pnl: msg.daily_pnl ?? 0 }].slice(-900)
        )
      }
      // Clear all histories when the server reconnected (instrument grid may have changed)
      const rc = msg.reconnect_count ?? 0
      if (rc !== prevReconnectRef.current && prevReconnectRef.current !== -1) {
        setItmCallHist([]); setAtmCallHist([]); setOtmCallHist([])
        setOtmPutHist([]);  setAtmPutHist([]);  setItmPutHist([])
      }
      prevReconnectRef.current = rc

      // Accumulate per-leg price histories (15-min window at 2s heartbeat = 450 ticks)
      const push = (setter: (fn: (p: number[]) => number[]) => void, val: number | undefined) => {
        if (val !== undefined && val !== null && val > 0)
          setter(prev => [...prev, val].slice(-450))
      }
      push(setItmCallHist, msg.itm_call_ltp)
      push(setAtmCallHist, msg.atm_ltp)
      push(setOtmCallHist, msg.otm_call_ltp)
      push(setOtmPutHist,  msg.otm_put_ltp)
      push(setAtmPutHist,  msg.atm_put_ltp)
      push(setItmPutHist,  msg.itm_put_ltp)
    } else if (msg.type === 'signal') {
      setLiveSignals(prev => [msg as Signal, ...prev].slice(0, 30))
      refreshSignals()
    } else if (msg.type === 'trade_open') {
      const { type: _t, ...trade } = msg
      setStatus(prev => ({ ...prev, open_trade: trade }))
      refreshTrades()
    } else if (msg.type === 'trade_close') {
      setStatus(prev => ({ ...prev, open_trade: null }))
      refreshTrades()
    }
  }, [refreshSignals, refreshTrades])

  const connected = useWebSocket(WS_URL, onMessage)

  // On every (re)connect: pre-populate all status fields from the HTTP snapshot
  // so the UI shows real engine state immediately instead of blanks for 2s.
  useEffect(() => {
    if (connected) {
      fetch('/api/status')
        .then(r => r.json())
        .then((d: Partial<StatusPayload> & { started_at?: number }) => {
          if (d.started_at) setServerStartedAt(d.started_at)
          setStatus(prev => ({ ...prev, ...d }))
        })
        .catch(() => {})
    }
  }, [connected])

  const displaySignals = liveSignals.length > 0 ? liveSignals : signals
  const pnl = status.daily_pnl ?? 0
  const pnlPct = status.daily_pnl_pct ?? 0
  const warmupPct = status.engine_state === 'WARMING_UP'
    ? Math.min((status.z_sample_count ?? 0) / 30 * 100, 100)
    : 100

  return (
    <div className="min-h-screen bg-surface text-white font-mono flex flex-col">

      {/* ── Top bar ── */}
      <header className="border-b border-border px-4 py-2 flex items-center gap-4 shrink-0 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-accent font-semibold">Market Sentinel</span>
          <span className="text-border">|</span>
          <span className={clsx('text-sm', ENGINE_COLORS[status.engine_state ?? 'IDLE'])}>
            ● {status.engine_state ?? 'IDLE'}
          </span>
        </div>

        <div className="flex items-center gap-4 ml-auto text-xs flex-wrap">
          {/* Uptime */}
          {uptime && (
            <div className="flex flex-col items-end">
              <span className="text-muted text-[9px] uppercase">Uptime</span>
              <span className="text-white">{uptime}</span>
            </div>
          )}
          {/* Spot */}
          <div className="flex flex-col items-end">
            <span className="text-muted text-[9px] uppercase">NIFTY Spot</span>
            <span className="text-white font-semibold">
              {status.spot_ltp ? fmt(status.spot_ltp, 2) : '—'}
            </span>
          </div>
          {/* ATM */}
          <div className="flex flex-col items-end">
            <span className="text-muted text-[9px] uppercase">ATM Option</span>
            <span>{status.atm_ltp ? fmt(status.atm_ltp, 2) : '—'}</span>
          </div>
          {/* Daily P&L */}
          <div className="flex flex-col items-end">
            <span className="text-muted text-[9px] uppercase">Day P&L</span>
            <span className={clsx('font-semibold', pnl >= 0 ? 'text-bull' : 'text-bear')}>
              {pnl >= 0 ? '+' : ''}₹{fmt(pnl, 0)}
              <span className="text-[10px] ml-1 opacity-70">({fmtPct(pnlPct)})</span>
            </span>
          </div>
          {/* Capital */}
          <div className="flex flex-col items-end">
            <span className="text-muted text-[9px] uppercase">Capital</span>
            <span>₹{status.capital ? fmt(status.capital, 0) : '—'}</span>
          </div>
          {/* Reconnects */}
          {(status.reconnect_count ?? 0) > 0 && (
            <div className="flex flex-col items-end">
              <span className="text-muted text-[9px] uppercase">Reconnects</span>
              <span className="text-warn">{status.reconnect_count}</span>
            </div>
          )}
          {/* WS dot */}
          <div
            className={clsx('w-2 h-2 rounded-full shrink-0', connected ? 'bg-bull animate-pulse' : 'bg-bear')}
            title={connected ? 'WebSocket connected' : 'Disconnected — reconnecting'}
          />
        </div>
      </header>

      {/* ── Warmup progress bar ── */}
      {status.engine_state === 'WARMING_UP' && (
        <div className="h-1 bg-border">
          <div
            className="h-full bg-warn transition-all duration-500"
            style={{ width: `${warmupPct}%` }}
          />
        </div>
      )}

      {/* ── Main grid ── */}
      <main className="flex-1 grid grid-cols-12 gap-3 p-3 min-h-0">

        {/* Left: Z-score + signal feed */}
        <aside className="col-span-3 flex flex-col gap-3">
          <div className="rounded-lg border border-border bg-panel p-3 flex flex-col items-center gap-2">
            <ZScoreGauge
              zScore={status.last_z_score ?? null}
              threshold={Z_THRESHOLD}
              sampleCount={status.z_sample_count ?? 0}
            />
            <div className="w-full grid grid-cols-2 gap-2 text-xs">
              <div className="bg-surface rounded p-2">
                <div className="text-muted text-[9px]">OTM CALL</div>
                <div className="text-bull font-semibold">
                  {status.otm_call_ltp ? fmt(status.otm_call_ltp) : '—'}
                </div>
              </div>
              <div className="bg-surface rounded p-2">
                <div className="text-muted text-[9px]">OTM PUT</div>
                <div className="text-bear font-semibold">
                  {status.otm_put_ltp ? fmt(status.otm_put_ltp) : '—'}
                </div>
              </div>
            </div>
          </div>

          {/* Market bias */}
          <MarketBias
            bias={status.market_bias ?? 'UNKNOWN'}
            triggerDelta={status.spot_delta_5m ?? null}
          />

          {/* Signal feed */}
          <div className="rounded-lg border border-border bg-panel p-3 flex-1 min-h-0 overflow-hidden">
            <SignalFeed signals={displaySignals} />
          </div>
        </aside>

        {/* Centre: charts + position */}
        <section className="col-span-6 flex flex-col gap-3">

          {/* Spot + Z-score chart */}
          <div className="rounded-lg border border-border bg-panel p-3 h-44">
            <div className="text-muted text-[9px] uppercase tracking-wider mb-1">
              Spot vs Z-Score (last 60s)
            </div>
            {priceHistory.length > 1 ? (
              <ResponsiveContainer width="100%" height="88%">
                <LineChart data={priceHistory}>
                  <XAxis dataKey="t" hide />
                  <YAxis yAxisId="spot" domain={['auto', 'auto']} width={58}
                         tick={{ fill: '#6b7280', fontSize: 9 }} />
                  <YAxis yAxisId="z" orientation="right" domain={[-4, 4]} width={28}
                         tick={{ fill: '#6b7280', fontSize: 9 }} />
                  <Tooltip
                    contentStyle={{ background: '#1a1d27', border: '1px solid #2a2d3a', fontSize: 10 }}
                    formatter={(v: number, name: string) =>
                      [fmt(v, name === 'z' ? 3 : 2), name === 'z' ? 'Z-Score' : 'Spot']}
                    labelFormatter={() => ''}
                  />
                  <ReferenceLine yAxisId="z" y={Z_THRESHOLD} stroke="#f59e0b" strokeDasharray="4 2" strokeWidth={1} />
                  <Line yAxisId="spot" type="monotone" dataKey="spot"
                        stroke="#6366f1" dot={false} strokeWidth={1.5} />
                  <Line yAxisId="z" type="monotone" dataKey="z"
                        stroke="#f59e0b" dot={false} strokeWidth={1} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-full text-muted text-sm">
                {status.engine_state === 'WARMING_UP' ? `Building baseline… ${status.z_sample_count ?? 0}/30 samples` : 'Waiting for data…'}
              </div>
            )}
          </div>

          {/* P&L sparkline */}
          <div className="rounded-lg border border-border bg-panel p-3 h-28">
            <div className="text-muted text-[9px] uppercase tracking-wider mb-1">
              Daily P&L (₹)
            </div>
            {pnlHistory.length > 1 ? (
              <ResponsiveContainer width="100%" height="80%">
                <LineChart data={pnlHistory}>
                  <XAxis dataKey="t" hide />
                  <YAxis domain={['auto', 'auto']} width={58}
                         tick={{ fill: '#6b7280', fontSize: 9 }} />
                  <Tooltip
                    contentStyle={{ background: '#1a1d27', border: '1px solid #2a2d3a', fontSize: 10 }}
                    formatter={(v: number) => [`₹${fmt(v, 0)}`, 'P&L']}
                    labelFormatter={() => ''}
                  />
                  <ReferenceLine y={0} stroke="#2a2d3a" strokeWidth={1} />
                  <Line type="monotone" dataKey="pnl"
                        stroke={pnl >= 0 ? '#22c55e' : '#ef4444'}
                        dot={false} strokeWidth={1.5} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-full text-muted text-xs">
                No P&L data yet
              </div>
            )}
          </div>

          {/* Option premium pressure */}
          <OptionPressure
            itmCallLtp={status.itm_call_ltp ?? 0}
            atmCallLtp={status.atm_ltp       ?? 0}
            otmCallLtp={status.otm_call_ltp  ?? 0}
            otmPutLtp ={status.otm_put_ltp   ?? 0}
            atmPutLtp ={status.atm_put_ltp   ?? 0}
            itmPutLtp ={status.itm_put_ltp   ?? 0}
            itmCallHistory={itmCallHist}
            atmCallHistory={atmCallHist}
            otmCallHistory={otmCallHist}
            otmPutHistory ={otmPutHist}
            atmPutHistory ={atmPutHist}
            itmPutHistory ={itmPutHist}
          />

          {/* Open position */}
          <PositionTracker
            trade={status.open_trade ?? null}
            atm_call_ltp={status.atm_ltp     ?? 0}
            atm_put_ltp ={status.atm_put_ltp ?? 0}
          />

          {/* Instrument map */}
          {status.instrument_map && (
            <div className="rounded-lg border border-border bg-panel p-3 text-xs">
              <div className="text-muted text-[9px] uppercase tracking-wider mb-2">Instrument Map</div>
              <div className="grid grid-cols-3 gap-2">
                <div className="bg-surface rounded p-2">
                  <div className="text-muted text-[9px]">ATM</div>
                  <div className="text-white font-semibold">{status.instrument_map.atm_strike}</div>
                </div>
                <div className="bg-surface rounded p-2">
                  <div className="text-bull text-[9px]">OTM Call +{status.instrument_map.otm_call_strike - status.instrument_map.atm_strike}</div>
                  <div className="text-white">Δ {status.instrument_map.otm_call_delta.toFixed(3)}</div>
                </div>
                <div className="bg-surface rounded p-2">
                  <div className="text-bear text-[9px]">OTM Put -{status.instrument_map.atm_strike - status.instrument_map.otm_put_strike}</div>
                  <div className="text-white">Δ {status.instrument_map.otm_put_delta.toFixed(3)}</div>
                </div>
              </div>
            </div>
          )}
        </section>

        {/* Right: blotter / config */}
        <aside className="col-span-3 flex flex-col">
          <div className="flex border-b border-border mb-3">
            {(['blotter', 'config'] as const).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={clsx(
                  'flex-1 py-1.5 text-xs uppercase tracking-wider transition-colors',
                  tab === t ? 'text-accent border-b-2 border-accent' : 'text-muted hover:text-white',
                )}
              >
                {t}
              </button>
            ))}
          </div>
          <div className="rounded-lg border border-border bg-panel p-3 flex-1 overflow-y-auto">
            {tab === 'blotter' ? <TradeBlotter trades={trades} /> : <ConfigPanel />}
          </div>
        </aside>
      </main>

      {/* ── Kill switch banner ── */}
      {status.engine_state === 'KILLED' && (
        <div className="bg-bear/20 border-t border-bear text-bear text-center py-2 text-sm font-semibold">
          ⚠ KILL SWITCH ACTIVE — Daily drawdown limit reached. Trading halted for this session.
        </div>
      )}

      {/* ── Warmup banner ── */}
      {status.engine_state === 'WARMING_UP' && (
        <div className="bg-warn/10 border-t border-warn/30 text-warn text-center py-1 text-xs">
          Warming up — building Z-score baseline ({status.z_sample_count ?? 0} / 30 samples)
          {' · '}closing the browser will NOT stop the engine
        </div>
      )}
    </div>
  )
}
