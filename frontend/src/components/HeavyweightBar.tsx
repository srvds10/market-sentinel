import clsx from 'clsx'

interface Stock {
  symbol: string
  ltp: number
  vwap: number | null
  above_vwap: boolean | null
  weight: number
}

interface Props {
  score: number | null
  direction: string
  stocks: Stock[]
}

const DIR_STYLE: Record<string, { label: string; cls: string; barCls: string }> = {
  BULLISH: { label: 'BULLISH',    cls: 'text-bull',  barCls: 'bg-bull'  },
  BEARISH: { label: 'BEARISH',    cls: 'text-bear',  barCls: 'bg-bear'  },
  NEUTRAL: { label: 'NEUTRAL',    cls: 'text-muted', barCls: 'bg-muted' },
  WAIT:    { label: 'COLLECTING', cls: 'text-muted', barCls: 'bg-muted' },
}

export function HeavyweightBar({ score, direction, stocks }: Props) {
  const d = DIR_STYLE[direction] ?? DIR_STYLE.WAIT

  // score in [-1, +1] → bar width 0-100%, centred at 50%
  const pct = score !== null ? Math.round(((score + 1) / 2) * 100) : 50
  const isLeft  = score !== null && score < 0
  const barLeft  = isLeft  ? `${pct}%`  : '50%'
  const barWidth = score !== null ? `${Math.abs(score) * 50}%` : '0%'

  return (
    <div className="rounded-lg border border-border bg-panel p-3">

      {/* Header row */}
      <div className="flex items-center justify-between mb-2">
        <div className="text-muted text-[9px] uppercase tracking-wider">
          NIFTY Heavyweights
        </div>
        <div className="flex items-center gap-2">
          {score !== null && (
            <span className="text-muted text-[9px]">
              score {score >= 0 ? '+' : ''}{score.toFixed(3)}
            </span>
          )}
          <span className={clsx('text-[10px] font-bold', d.cls)}>{d.label}</span>
        </div>
      </div>

      {/* Score bar */}
      <div className="relative h-2 bg-surface rounded-full mb-3 overflow-hidden">
        {/* centre tick */}
        <div className="absolute top-0 bottom-0 w-px bg-border" style={{ left: '50%' }} />
        {/* coloured fill */}
        <div
          className={clsx('absolute top-0 bottom-0 rounded-full transition-all duration-500', d.barCls, 'opacity-70')}
          style={{ left: barLeft, width: barWidth }}
        />
      </div>

      {/* Stock dots grid */}
      <div className="grid grid-cols-5 gap-1">
        {stocks.map(s => (
          <div key={s.symbol} className="flex flex-col items-center gap-0.5">
            <div className={clsx(
              'w-2.5 h-2.5 rounded-full',
              s.above_vwap === true  ? 'bg-bull' :
              s.above_vwap === false ? 'bg-bear' : 'bg-border',
            )} title={
              s.vwap !== null
                ? `${s.symbol} LTP ${s.ltp} | VWAP ${s.vwap} | ${s.above_vwap ? 'above' : 'below'}`
                : `${s.symbol} — no data`
            } />
            <span className="text-[7px] text-muted leading-none">{s.symbol.slice(0, 6)}</span>
            <span className="text-[7px] text-muted leading-none opacity-60">{s.weight}%</span>
          </div>
        ))}
      </div>

      <div className="mt-1.5 text-[8px] text-muted text-center">
        {stocks.filter(s => s.above_vwap === true).length} above VWAP ·{' '}
        {stocks.filter(s => s.above_vwap === false).length} below ·{' '}
        {stocks.filter(s => s.above_vwap === null).length} waiting
      </div>
    </div>
  )
}
