import { useCallback, useState } from 'react'
import clsx from 'clsx'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'

import { ZScoreGauge } from './components/ZScoreGauge'
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

export default function App() {
  const [status, setStatus] = useState<Partial<StatusPayload>>({})
  const [priceHistory, setPriceHistory] = useState<Array<{ t: number; spot: number; z: number }>>([])
  const [liveSignals, setLiveSignals] = useState<Signal[]>([])
  const [tab, setTab] = useState<'blotter' | 'config'>('blotter')

  const { trades, refresh: refreshTrades } = useTrades(100)
  const { signals, refresh: refreshSignals } = useSignals(30)

  const onMessage = useCallback((msg: WSMessage) => {
    if (msg.type === 'status') {
      setStatus(prev => ({ ...prev, ...msg }))
      if (msg.spot_ltp) {
        setPriceHistory(prev => {
          const next = [
            ...prev,
            { t: Date.now(), spot: msg.spot_ltp!, z: msg.last_z_score ?? 0 },
          ].slice(-120)   // keep last 120 ticks (~60s)
          return next
        })
      }
    } else if (msg.type === 'signal') {
      setLiveSignals(prev => [msg as Signal, ...prev].slice(0, 30))
      refreshSignals()
    } else if (msg.type === 'trade_open' || msg.type === 'trade_close') {
      refreshTrades()
    }
  }, [refreshSignals, refreshTrades])

  const connected = useWebSocket(WS_URL, onMessage)

  const displaySignals = liveSignals.length > 0 ? liveSignals : signals
  const pnl = status.daily_pnl ?? 0
  const pnlPct = status.daily_pnl_pct ?? 0

  return (
    <div className="min-h-screen bg-surface text-white font-mono flex flex-col">
      {/* ── Top bar ── */}
      <header className="border-b border-border px-6 py-3 flex items-center gap-6 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-accent font-semibold text-base">Market Sentinel</span>
          <span className="text-border">|</span>
          <span className={clsx('text-sm', ENGINE_COLORS[status.engine_state ?? 'IDLE'])}>
            ● {status.engine_state ?? 'IDLE'}
          </span>
        </div>

        <div className="flex items-center gap-5 ml-auto text-sm">
          {/* Spot */}
          <div className="flex flex-col items-end">
            <span className="text-muted text-[10px] uppercase">NIFTY</span>
            <span className="text-white font-semibold">
              {status.spot_ltp ? fmt(status.spot_ltp, 2) : '—'}
            </span>
          </div>
          {/* ATM */}
          <div className="flex flex-col items-end">
            <span className="text-muted text-[10px] uppercase">ATM</span>
            <span>{status.atm_ltp ? fmt(status.atm_ltp, 2) : '—'}</span>
          </div>
          {/* Daily P&L */}
          <div className="flex flex-col items-end">
            <span className="text-muted text-[10px] uppercase">Day P&L</span>
            <span className={clsx('font-semibold', pnl >= 0 ? 'text-bull' : 'text-bear')}>
              {pnl >= 0 ? '+' : ''}₹{fmt(pnl, 0)}
              <span className="text-xs ml-1 opacity-70">({fmtPct(pnlPct)})</span>
            </span>
          </div>
          {/* Capital */}
          <div className="flex flex-col items-end">
            <span className="text-muted text-[10px] uppercase">Capital</span>
            <span>₹{status.capital ? fmt(status.capital, 0) : '—'}</span>
          </div>
          {/* WS indicator */}
          <div className={clsx('w-2 h-2 rounded-full', connected ? 'bg-bull' : 'bg-bear')}
               title={connected ? 'Connected' : 'Disconnected'} />
        </div>
      </header>

      {/* ── Main grid ── */}
      <main className="flex-1 grid grid-cols-12 gap-4 p-4 min-h-0">

        {/* Left column: Z-score + signal feed */}
        <aside className="col-span-3 flex flex-col gap-4">
          <div className="rounded-lg border border-border bg-panel p-4 flex flex-col items-center gap-2">
            <ZScoreGauge
              zScore={status.last_z_score ?? null}
              threshold={Z_THRESHOLD}
              sampleCount={status.z_sample_count ?? 0}
            />
            {/* OTM LTPs */}
            <div className="w-full grid grid-cols-2 gap-2 text-xs mt-1">
              <div className="bg-surface rounded p-2">
                <div className="text-muted text-[10px]">OTM CALL</div>
                <div className="text-bull font-semibold">
                  {status.otm_call_ltp ? fmt(status.otm_call_ltp) : '—'}
                </div>
              </div>
              <div className="bg-surface rounded p-2">
                <div className="text-muted text-[10px]">OTM PUT</div>
                <div className="text-bear font-semibold">
                  {status.otm_put_ltp ? fmt(status.otm_put_ltp) : '—'}
                </div>
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-panel p-4 flex-1 min-h-0 overflow-hidden">
            <SignalFeed signals={displaySignals} />
          </div>
        </aside>

        {/* Centre: price chart + position */}
        <section className="col-span-6 flex flex-col gap-4">
          {/* Price + Z-score chart */}
          <div className="rounded-lg border border-border bg-panel p-4 h-56">
            <div className="text-muted text-xs uppercase tracking-wider mb-2">
              Spot vs Z-Score (last 60s)
            </div>
            {priceHistory.length > 1 ? (
              <ResponsiveContainer width="100%" height="85%">
                <LineChart data={priceHistory}>
                  <XAxis dataKey="t" hide />
                  <YAxis yAxisId="spot" domain={['auto', 'auto']} width={60}
                         tick={{ fill: '#6b7280', fontSize: 10 }} />
                  <YAxis yAxisId="z" orientation="right" domain={[-3, 3]} width={30}
                         tick={{ fill: '#6b7280', fontSize: 10 }} />
                  <Tooltip
                    contentStyle={{ background: '#1a1d27', border: '1px solid #2a2d3a', fontSize: 11 }}
                    formatter={(v: number, name: string) =>
                      [fmt(v, name === 'z' ? 3 : 2), name === 'z' ? 'Z-Score' : 'Spot']}
                    labelFormatter={() => ''}
                  />
                  <Line yAxisId="spot" type="monotone" dataKey="spot"
                        stroke="#6366f1" dot={false} strokeWidth={1.5} />
                  <Line yAxisId="z"    type="monotone" dataKey="z"
                        stroke="#f59e0b" dot={false} strokeWidth={1} strokeDasharray="3 2" />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-full text-muted text-sm">
                Waiting for data…
              </div>
            )}
          </div>

          {/* Open position */}
          <PositionTracker trade={status.open_trade ?? null} atm_ltp={status.atm_ltp ?? 0} />

          {/* Instrument map */}
          {status.instrument_map && (
            <div className="rounded-lg border border-border bg-panel p-3 text-xs">
              <div className="text-muted text-[10px] uppercase tracking-wider mb-2">
                Instrument Map
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div className="bg-surface rounded p-2">
                  <div className="text-muted">ATM</div>
                  <div className="text-white font-semibold">{status.instrument_map.atm_strike}</div>
                </div>
                <div className="bg-surface rounded p-2">
                  <div className="text-bull">OTM Call +{status.instrument_map.otm_call_strike - status.instrument_map.atm_strike}</div>
                  <div className="text-white">Δ {status.instrument_map.otm_call_delta.toFixed(3)}</div>
                </div>
                <div className="bg-surface rounded p-2">
                  <div className="text-bear">OTM Put -{status.instrument_map.atm_strike - status.instrument_map.otm_put_strike}</div>
                  <div className="text-white">Δ {status.instrument_map.otm_put_delta.toFixed(3)}</div>
                </div>
              </div>
            </div>
          )}
        </section>

        {/* Right column: blotter / config tabs */}
        <aside className="col-span-3 flex flex-col gap-0">
          <div className="flex border-b border-border mb-3">
            {(['blotter', 'config'] as const).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={clsx(
                  'flex-1 py-1.5 text-xs uppercase tracking-wider transition-colors',
                  tab === t
                    ? 'text-accent border-b-2 border-accent'
                    : 'text-muted hover:text-white',
                )}
              >
                {t}
              </button>
            ))}
          </div>

          <div className="rounded-lg border border-border bg-panel p-4 flex-1 overflow-y-auto">
            {tab === 'blotter'
              ? <TradeBlotter trades={trades} />
              : <ConfigPanel />
            }
          </div>
        </aside>
      </main>

      {/* Kill switch banner */}
      {(status.engine_state === 'KILLED') && (
        <div className="bg-bear/20 border-t border-bear text-bear text-center py-2 text-sm font-semibold">
          ⚠ KILL SWITCH ACTIVE — Daily drawdown limit reached. Trading halted for this session.
        </div>
      )}

      {/* Warmup banner */}
      {status.engine_state === 'WARMING_UP' && (
        <div className="bg-warn/10 border-t border-warn/30 text-warn text-center py-1 text-xs">
          Warming up — building Z-score baseline ({status.z_sample_count ?? 0} samples collected)
        </div>
      )}
    </div>
  )
}
