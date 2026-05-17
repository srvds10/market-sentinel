import { useMemo } from 'react'
import clsx from 'clsx'

// ---------------------------------------------------------------------------
// Momentum calculation
// ---------------------------------------------------------------------------

type Pressure = 'EXPANDING' | 'SQUEEZING' | 'FLAT'

function getPressure(history: number[]): Pressure {
  const pts = history.filter(v => v > 0)
  if (pts.length < 5) return 'FLAT'
  const half = Math.floor(pts.length / 2)
  const oldAvg = pts.slice(0, half).reduce((a, b) => a + b, 0) / half
  const newAvg = pts.slice(-half).reduce((a, b) => a + b, 0) / half
  if (oldAvg === 0) return 'FLAT'
  const pct = (newAvg - oldAvg) / oldAvg
  if (pct >  0.0015) return 'EXPANDING'
  if (pct < -0.0015) return 'SQUEEZING'
  return 'FLAT'
}

const PRESSURE: Record<Pressure, { arrow: string; cls: string; label: string }> = {
  EXPANDING: { arrow: '▲', cls: 'text-bull',  label: 'EXPAND' },
  SQUEEZING: { arrow: '▼', cls: 'text-bear',  label: 'SQUEEZE' },
  FLAT:      { arrow: '─', cls: 'text-muted', label: 'FLAT' },
}

// ---------------------------------------------------------------------------
// Single leg cell
// ---------------------------------------------------------------------------

interface LegProps {
  moneyness: 'ITM' | 'ATM' | 'OTM'
  side: 'CE' | 'PE'
  ltp: number
  history: number[]
}

function LegCell({ moneyness, side, ltp, history }: LegProps) {
  const pressure = useMemo(() => getPressure(history), [history])
  const p = PRESSURE[pressure]
  const sideColor = side === 'CE' ? 'text-bull' : 'text-bear'
  const hasData = ltp > 0

  return (
    <div className="bg-surface rounded p-2 flex flex-col gap-0.5 min-w-0">
      <div className="flex items-center gap-1">
        <span className={clsx('text-[9px] font-bold', sideColor)}>{moneyness}</span>
        <span className="text-muted text-[9px]">{side}</span>
      </div>
      <div className="text-white text-xs font-semibold tabular-nums leading-tight">
        {hasData ? ltp.toFixed(2) : '—'}
      </div>
      <div className={clsx('text-[9px] flex items-center gap-0.5 font-medium', hasData ? p.cls : 'text-muted')}>
        <span>{hasData ? p.arrow : '·'}</span>
        <span>{hasData ? p.label : 'NO DATA'}</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export interface OptionPressureProps {
  itmCallLtp: number
  atmCallLtp: number
  otmCallLtp: number
  otmPutLtp:  number
  atmPutLtp:  number
  itmPutLtp:  number
  itmCallHistory: number[]
  atmCallHistory: number[]
  otmCallHistory: number[]
  otmPutHistory:  number[]
  atmPutHistory:  number[]
  itmPutHistory:  number[]
}

export function OptionPressure(props: OptionPressureProps) {
  const legs: LegProps[] = [
    { moneyness: 'ITM', side: 'CE', ltp: props.itmCallLtp, history: props.itmCallHistory },
    { moneyness: 'ATM', side: 'CE', ltp: props.atmCallLtp, history: props.atmCallHistory },
    { moneyness: 'OTM', side: 'CE', ltp: props.otmCallLtp, history: props.otmCallHistory },
    { moneyness: 'OTM', side: 'PE', ltp: props.otmPutLtp,  history: props.otmPutHistory  },
    { moneyness: 'ATM', side: 'PE', ltp: props.atmPutLtp,  history: props.atmPutHistory  },
    { moneyness: 'ITM', side: 'PE', ltp: props.itmPutLtp,  history: props.itmPutHistory  },
  ]

  return (
    <div className="rounded-lg border border-border bg-panel p-3">
      <div className="text-muted text-[9px] uppercase tracking-wider mb-2">
        Option Premium Pressure
      </div>
      <div className="grid grid-cols-6 gap-1.5">
        {legs.map((leg, i) => <LegCell key={i} {...leg} />)}
      </div>
    </div>
  )
}
