import { useMemo } from 'react'
import clsx from 'clsx'

// ---------------------------------------------------------------------------
// Per-minute snapshot pressure calculation
//
// Every 60s the engine pushes the current LTP for each leg into a 15-slot
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

/** Returns the dominant pressure for a group of legs (majority vote). */
function groupPressure(pressures: Pressure[]): Pressure {
  const active = pressures.filter(p => p !== 'WAIT')
  if (active.length === 0) return 'WAIT'
  const exp = active.filter(p => p === 'EXPANDING').length
  const sqz = active.filter(p => p === 'SQUEEZING').length
  if (exp > sqz && exp >= Math.ceil(active.length / 2)) return 'EXPANDING'
  if (sqz > exp && sqz >= Math.ceil(active.length / 2)) return 'SQUEEZING'
  return 'FLAT'
}

const STYLE: Record<Pressure, { arrow: string; cls: string; label: string }> = {
  EXPANDING: { arrow: '▲', cls: 'text-bull',  label: 'EXPAND'  },
  SQUEEZING: { arrow: '▼', cls: 'text-bear',  label: 'SQUEEZE' },
  FLAT:      { arrow: '─', cls: 'text-muted', label: 'FLAT'    },
  WAIT:      { arrow: '○', cls: 'text-muted', label: 'WAIT'    },
}

// ---------------------------------------------------------------------------
// Combined verdict
// ---------------------------------------------------------------------------

type Verdict =
  | 'BOTH_EXPAND'
  | 'BOTH_SQUEEZE'
  | 'CALL_DOMINANT'
  | 'PUT_DOMINANT'
  | 'MIXED'
  | 'WAIT'

interface VerdictDef {
  label: string
  sub: string
  badgeCls: string
  borderCls: string
}

const VERDICT_DEF: Record<Verdict, VerdictDef> = {
  BOTH_EXPAND:   { label: 'VOLATILITY SPIKE',  sub: 'All premiums rising',      badgeCls: 'bg-warn/20 text-warn',        borderCls: 'border-warn/40'   },
  BOTH_SQUEEZE:  { label: 'PREMIUM DECAY',     sub: 'All premiums falling',     badgeCls: 'bg-muted/20 text-muted',      borderCls: 'border-border'    },
  CALL_DOMINANT: { label: 'CALL PRESSURE',     sub: 'Calls up · Puts down',     badgeCls: 'bg-bull/20 text-bull',        borderCls: 'border-bull/40'   },
  PUT_DOMINANT:  { label: 'PUT PRESSURE',      sub: 'Puts up · Calls down',     badgeCls: 'bg-bear/20 text-bear',        borderCls: 'border-bear/40'   },
  MIXED:         { label: 'MIXED',             sub: 'No clear consensus',       badgeCls: 'bg-muted/10 text-muted',      borderCls: 'border-border'    },
  WAIT:          { label: 'COLLECTING…',       sub: 'Need ≥ 3 min of data',     badgeCls: 'bg-muted/10 text-muted',      borderCls: 'border-border'    },
}

function getVerdict(callP: Pressure, putP: Pressure): Verdict {
  if (callP === 'WAIT' && putP === 'WAIT') return 'WAIT'
  if (callP === 'EXPANDING' && putP === 'EXPANDING') return 'BOTH_EXPAND'
  if (callP === 'SQUEEZING' && putP === 'SQUEEZING') return 'BOTH_SQUEEZE'
  if (callP === 'EXPANDING' && putP === 'SQUEEZING') return 'CALL_DOMINANT'
  if (callP === 'SQUEEZING' && putP === 'EXPANDING') return 'PUT_DOMINANT'
  return 'MIXED'
}

// ---------------------------------------------------------------------------
// Dot indicator (used in composite bar)
// ---------------------------------------------------------------------------

const DOT_CLS: Record<Pressure, string> = {
  EXPANDING: 'bg-bull',
  SQUEEZING: 'bg-bear',
  FLAT:      'bg-muted',
  WAIT:      'bg-border',
}

function PressureDot({ pressure, label }: { pressure: Pressure; label: string }) {
  return (
    <div className="flex flex-col items-center gap-0.5">
      <div className={clsx('w-2 h-2 rounded-full', DOT_CLS[pressure])} title={label} />
      <span className="text-[7px] text-muted">{label}</span>
    </div>
  )
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
// Composite summary bar
// ---------------------------------------------------------------------------

interface CompositeBarProps {
  callPressures: Pressure[]  // [ITM-CE, ATM-CE, OTM-CE]
  putPressures:  Pressure[]  // [OTM-PE, ATM-PE, ITM-PE]
}

function CompositeBar({ callPressures, putPressures }: CompositeBarProps) {
  const callGroup = groupPressure(callPressures)
  const putGroup  = groupPressure(putPressures)
  const verdict   = getVerdict(callGroup, putGroup)
  const vd        = VERDICT_DEF[verdict]
  const callStyle = STYLE[callGroup]
  const putStyle  = STYLE[putGroup]
  const callDotLabels = ['ITM', 'ATM', 'OTM']
  const putDotLabels  = ['OTM', 'ATM', 'ITM']

  return (
    <div className={clsx('mt-2 rounded border p-2 flex items-center gap-3', vd.borderCls)}>

      {/* CALLS side */}
      <div className="flex flex-col items-center gap-1 shrink-0">
        <span className="text-[8px] text-bull font-bold uppercase tracking-wider">Calls</span>
        <div className="flex gap-1.5">
          {callPressures.map((p, i) => (
            <PressureDot key={i} pressure={p} label={callDotLabels[i]} />
          ))}
        </div>
        <span className={clsx('text-[9px] font-semibold flex items-center gap-0.5', callStyle.cls)}>
          <span>{callStyle.arrow}</span>
          <span>{callStyle.label}</span>
        </span>
      </div>

      {/* Verdict badge — centre */}
      <div className="flex-1 flex flex-col items-center gap-0.5">
        <span className={clsx('px-2 py-0.5 rounded text-[10px] font-bold tracking-wide', vd.badgeCls)}>
          {vd.label}
        </span>
        <span className="text-[8px] text-muted">{vd.sub}</span>
      </div>

      {/* PUTS side */}
      <div className="flex flex-col items-center gap-1 shrink-0">
        <span className="text-[8px] text-bear font-bold uppercase tracking-wider">Puts</span>
        <div className="flex gap-1.5">
          {putPressures.map((p, i) => (
            <PressureDot key={i} pressure={p} label={putDotLabels[i]} />
          ))}
        </div>
        <span className={clsx('text-[9px] font-semibold flex items-center gap-0.5', putStyle.cls)}>
          <span>{putStyle.arrow}</span>
          <span>{putStyle.label}</span>
        </span>
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

  const callPressures: Pressure[] = [
    getPressure(props.itmCallMins),
    getPressure(props.atmCallMins),
    getPressure(props.otmCallMins),
  ]
  const putPressures: Pressure[] = [
    getPressure(props.otmPutMins),
    getPressure(props.atmPutMins),
    getPressure(props.itmPutMins),
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
      <CompositeBar callPressures={callPressures} putPressures={putPressures} />
    </div>
  )
}
