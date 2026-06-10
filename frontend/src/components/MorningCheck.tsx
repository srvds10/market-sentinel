import clsx from 'clsx'

interface Props {
  verdict: string
  driftPct: number | null
  efficiency: number | null
  snapTaken: boolean
  straddleOpen: number
}

const VERDICT_CONFIG = {
  BUY_OK:  { label: 'BUY OK',  cls: 'text-bull', barCls: 'bg-bull',  border: 'border-bull/30',  bg: 'bg-bull/5'  },
  CAUTION: { label: 'CAUTION', cls: 'text-warn', barCls: 'bg-warn',  border: 'border-warn/30',  bg: 'bg-warn/5'  },
  AVOID:   { label: 'AVOID',   cls: 'text-bear', barCls: 'bg-bear',  border: 'border-bear/30',  bg: 'bg-bear/5'  },
  WAIT:    { label: 'WAIT',    cls: 'text-muted', barCls: 'bg-muted', border: 'border-border',   bg: 'bg-panel'   },
}

function DriftBar({ driftPct }: { driftPct: number }) {
  // Bar shows drift from -15% (full left) to +5% (full right), centre = 0%
  const MIN = -15
  const MAX = 5
  const clampedPct = Math.max(MIN, Math.min(MAX, driftPct))
  // Zero line is at (0 - MIN) / (MAX - MIN) = 15/20 = 75% from left
  const zeroX = ((0 - MIN) / (MAX - MIN)) * 100
  // Bar fills from zero to current value
  const filled = ((clampedPct - MIN) / (MAX - MIN)) * 100
  const barLeft  = Math.min(zeroX, filled)
  const barWidth = Math.abs(filled - zeroX)
  const barColor = driftPct >= -3 ? 'bg-bull' : driftPct >= -8 ? 'bg-warn' : 'bg-bear'

  return (
    <div className="relative h-1.5 bg-surface rounded-full overflow-hidden mt-1 mb-0.5">
      {/* zero line */}
      <div className="absolute top-0 bottom-0 w-px bg-border/60" style={{ left: `${zeroX}%` }} />
      {/* drift bar */}
      <div
        className={clsx('absolute top-0 bottom-0 rounded-full transition-all duration-500', barColor)}
        style={{ left: `${barLeft}%`, width: `${barWidth}%` }}
      />
    </div>
  )
}

export function MorningCheck({ verdict, driftPct, efficiency, snapTaken, straddleOpen }: Props) {
  const cfg = VERDICT_CONFIG[verdict as keyof typeof VERDICT_CONFIG] ?? VERDICT_CONFIG.WAIT

  return (
    <div className={clsx('rounded-lg border p-3', cfg.border, cfg.bg)}>
      <div className="flex items-center justify-between mb-2">
        <div className="text-muted text-[9px] uppercase tracking-wider">Morning Check</div>
        <span className={clsx('text-[10px] font-bold', cfg.cls)}>{cfg.label}</span>
      </div>

      {!snapTaken ? (
        <div className="text-muted text-[9px]">
          Waiting for 09:16 snap…
        </div>
      ) : (
        <>
          {/* Straddle drift */}
          <div className="flex items-center justify-between text-[9px] mb-0.5">
            <span className="text-muted">Straddle drift</span>
            <span className={clsx('font-semibold tabular-nums', cfg.cls)}>
              {driftPct !== null
                ? `${driftPct >= 0 ? '+' : ''}${driftPct.toFixed(1)}%`
                : '—'}
            </span>
          </div>

          {driftPct !== null && <DriftBar driftPct={driftPct} />}

          <div className="flex items-center justify-between text-[8px] text-muted mt-1">
            <span>−15%</span>
            <span>0%</span>
            <span>+5%</span>
          </div>

          {/* Option efficiency (only when spot has moved) */}
          {efficiency !== null && (
            <div className="flex items-center justify-between text-[9px] mt-2 pt-2 border-t border-border">
              <span className="text-muted">Call efficiency</span>
              <span className={clsx(
                'font-semibold tabular-nums',
                efficiency > 0 ? 'text-bull' : efficiency < 0 ? 'text-bear' : 'text-muted',
              )}>
                {efficiency > 0 ? '+' : ''}{efficiency.toFixed(2)}×
              </span>
            </div>
          )}

          {/* Threshold legend */}
          <div className="mt-2 pt-2 border-t border-border grid grid-cols-3 gap-1 text-[7px] text-center">
            <div className="text-bull">▸ −3% BUY OK</div>
            <div className="text-warn">▸ −8% CAUTION</div>
            <div className="text-bear">▸ below AVOID</div>
          </div>
        </>
      )}
    </div>
  )
}
