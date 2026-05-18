import { useMemo } from 'react'
import clsx from 'clsx'

// ---------------------------------------------------------------------------
// Per-minute snapshot pressure calculation
//
// Every 60s App.tsx pushes the current LTP for each leg into a 15-slot
// ring buffer.  Here we compare the most recent 1-min snapshot to the
// rolling 15-min mean: if the latest price is meaningfully above the mean
// it is EXPANDING; below = SQUEEZING; within threshold = FLAT.
// We wait for at least 3 minutes of data before signalling.
// ---------------------------------------------------------------------------

type Pressure = 'EXPANDING' | 'SQUEEZING' | 'FLAT' | 'WAIT'

const THRESHOLD = 0.01  // 1 % deviation from mean triggers a signal

function getPressure(minutes: number[]): Pressure {
  const pts = minutes.filter(v => v > 0)
  if (pts.length < 3) return 'WAIT'
  const mean = pts.reduce((a, b) => a + b, 0) / pts.length
  if (mean === 0) return 'WAIT'
  const latest = pts[pts.length - 1]
  const pct = (latest - mean) / mean
  if (pct >  THRESHOLD) return 'EXPANDING'
  if (pct < -THRESHOLD) return 'SQUEEZING'
  return 'FLAT'
}

const STYLE: Record<Pressure, { arrow: string; cls: string; label: string }> = {
  EXPANDING: { arrow: '▲', cls: 'text-bull',  label: 'EXPAND'  },
  SQUEEZING: { arrow: '▼', cls: 'text-bear',  label: 'SQUEEZE' },
  FLAT:      { arrow: '─', cls: 'text-muted', label: 'FLAT'    },
  WAIT:      { arrow: '○', cls: 'text-muted', label: 'WAIT'    },
}

// ---------------------------------------------------------------------------
// Single leg cell
// ---------------------------------------------------------------------------

interface LegProps {
  moneyness: 'ITM' | 'ATM' | 'OTM'
  side: 'CE' | 'PE'
  ltp: number         // live price (updates every 2s)
  minutes: number[]   // per-minute snapshots (max 15)
}

function LegCell({ moneyness, side, ltp, minutes }: LegProps) {
  const pressure = useMemo(() => getPressure(minutes), [minutes])
  const s = STYLE[pressure]
  const sideColor = side === 'CE' ? 'text-bull' : 'text-bear'
  const hasData = ltp > 0

  // Show deviation from mean alongside the signal
  const deviation = useMemo(() => {
    const pts = minutes.filter(v => v > 0)
    if (pts.length < 3) return null
    const mean = pts.reduce((a, b) => a + b, 0) / pts.length
    if (mean === 0) return null
    return ((pts[pts.length - 1] - mean) / mean * 100).toFixed(1)
  }, [minutes])

  return (
    <div className="bg-surface rounded p-2 flex flex-col gap-0.5 min-w-0">
      <div className="flex items-center gap-1">
        <span className={clsx('text-[9px] font-bold', sideColor)}>{moneyness}</span>
        <span className="text-muted text-[9px]">{side}</span>
      </div>
      <div className="text-white text-xs font-semibold tabular-nums leading-tight">
        {hasData ? ltp.toFixed(2) : '—'}
      </div>
      <div className={clsx('text-[9px] flex items-center gap-0.5 font-medium', hasData ? s.cls : 'text-muted')}>
        <span>{hasData ? s.arrow : '·'}</span>
        <span>{hasData ? s.label : 'NO DATA'}</span>
        {deviation !== null && (
          <span className="opacity-60">({deviation}%)</span>
        )}
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
  itmCallMins: number[]
  atmCallMins: number[]
  otmCallMins: number[]
  otmPutMins:  number[]
  atmPutMins:  number[]
  itmPutMins:  number[]
}

export function OptionPressure(props: OptionPressureProps) {
  const sampleCount = Math.max(
    props.itmCallMins.length, props.atmCallMins.length, props.otmCallMins.length,
    props.otmPutMins.length,  props.atmPutMins.length,  props.itmPutMins.length,
  )

  const legs: LegProps[] = [
    { moneyness: 'ITM', side: 'CE', ltp: props.itmCallLtp, minutes: props.itmCallMins },
    { moneyness: 'ATM', side: 'CE', ltp: props.atmCallLtp, minutes: props.atmCallMins },
    { moneyness: 'OTM', side: 'CE', ltp: props.otmCallLtp, minutes: props.otmCallMins },
    { moneyness: 'OTM', side: 'PE', ltp: props.otmPutLtp,  minutes: props.otmPutMins  },
    { moneyness: 'ATM', side: 'PE', ltp: props.atmPutLtp,  minutes: props.atmPutMins  },
    { moneyness: 'ITM', side: 'PE', ltp: props.itmPutLtp,  minutes: props.itmPutMins  },
  ]

  return (
    <div className="rounded-lg border border-border bg-panel p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-muted text-[9px] uppercase tracking-wider">
          Option Premium Pressure
        </div>
        <div className="text-muted text-[9px]">
          {sampleCount > 0
            ? `${sampleCount}/15 min · vs 15-min avg`
            : 'collecting…'}
        </div>
      </div>
      <div className="grid grid-cols-6 gap-1.5">
        {legs.map((leg, i) => <LegCell key={i} {...leg} />)}
      </div>
    </div>
  )
}
