import clsx from 'clsx'

interface Props {
  zScore: number | null
  marketBias: string
  aboveVwap: boolean | null
  pressureVerdict: string
  hwDirection: string
  pcrSentiment: string
  pcrStale: boolean
  engineState: string
}

// Z-score and market bias are the primary directional signals — weighted 2x.
// VWAP is session-level trend (slow), option pressure and heavyweight are
// corroborating context, PCR is lagging. All secondary signals = 1x.
const SIGNALS: Array<{
  key: string
  label: string
  weight: 2 | 1
  title: string
}> = [
  { key: 'z',    label: 'Z',    weight: 2, title: 'Z-Score (primary)' },
  { key: 'bias', label: 'BIAS', weight: 2, title: 'Market Bias (spot Δ5m)' },
  { key: 'vwap', label: 'VWAP', weight: 1, title: 'VWAP position' },
  { key: 'opt',  label: 'OPT',  weight: 1, title: 'Option pressure' },
  { key: 'hw',   label: 'HW',   weight: 1, title: 'Heavyweight stocks' },
  { key: 'pcr',  label: 'PCR',  weight: 1, title: 'Put-Call Ratio (lagging)' },
]
const MAX_SCORE = SIGNALS.reduce((s, sig) => s + sig.weight, 0) // 8

function computeVotes(p: Props): Record<string, 1 | 0 | -1> {
  const z = p.zScore
  return {
    // Use 2.0 — same threshold the engine uses to take trades
    z:    z === null ? 0 : z >= 2.0 ? 1 : z <= -2.0 ? -1 : 0,
    bias: p.marketBias === 'BULLISH' ? 1 : p.marketBias === 'BEARISH' ? -1 : 0,
    vwap: p.aboveVwap === true ? 1 : p.aboveVwap === false ? -1 : 0,
    opt:  p.pressureVerdict === 'CALL_DOMINANT' ? 1
        : p.pressureVerdict === 'PUT_DOMINANT'  ? -1
        : 0,
    hw:   p.hwDirection === 'BULLISH' ? 1 : p.hwDirection === 'BEARISH' ? -1 : 0,
    // Skip PCR vote when stale — don't let a 5-min-old reading sway the verdict
    pcr:  p.pcrStale             ? 0
        : p.pcrSentiment === 'CALL_HEAVY' ? 1
        : p.pcrSentiment === 'PUT_HEAVY'  ? -1
        : 0,
  }
}

export function MarketSignal(props: Props) {
  const ready = props.engineState === 'ACTIVE'

  if (!ready) {
    return (
      <div className="rounded-lg border border-border bg-panel p-3">
        <div className="text-muted text-[9px] uppercase tracking-wider mb-1">Market Signal</div>
        <div className="text-muted text-sm font-semibold">— WAIT</div>
        <div className="text-muted text-[9px] mt-1">
          {props.engineState === 'WARMING_UP' ? 'Building baseline…' : 'Engine not active'}
        </div>
      </div>
    )
  }

  const votes = computeVotes(props)
  const score = SIGNALS.reduce((s, sig) => s + votes[sig.key] * sig.weight, 0)
  const absScore = Math.abs(score)

  // Require ≥50% of weighted score to declare a direction (threshold = 4/8)
  const direction: 'UP' | 'DOWN' | 'SIDEWAYS' =
    score >= 4 ? 'UP' : score <= -4 ? 'DOWN' : 'SIDEWAYS'

  const strength =
    absScore >= 7 ? 'STRONG' :
    absScore >= 5 ? 'HIGH'   :
    absScore >= 4 ? 'MOD'    : ''

  // True when z-score has actually crossed the engine's trade threshold
  const tradeZone = props.zScore !== null && Math.abs(props.zScore) >= 2.0

  const dirCls =
    direction === 'UP'   ? 'text-bull' :
    direction === 'DOWN' ? 'text-bear' :
    'text-muted'

  const dirLabel =
    direction === 'UP'   ? '▲ UP'       :
    direction === 'DOWN' ? '▼ DOWN'     :
    '● SIDEWAYS'

  const strengthCls =
    direction === 'UP'   ? 'bg-bull/15 text-bull'  :
    direction === 'DOWN' ? 'bg-bear/15 text-bear'  :
    'bg-muted/10 text-muted'

  return (
    <div className="rounded-lg border border-border bg-panel p-3">
      <div className="text-muted text-[9px] uppercase tracking-wider mb-2">Market Signal</div>

      {/* Direction + tags */}
      <div className="flex items-center gap-2 mb-1 flex-wrap">
        <span className={clsx('text-xl font-bold leading-none', dirCls)}>
          {dirLabel}
        </span>
        {strength && (
          <span className={clsx('text-[9px] font-semibold px-1.5 py-0.5 rounded', strengthCls)}>
            {strength}
          </span>
        )}
        {tradeZone && (
          <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded bg-warn/15 text-warn"
                title="Z-score has crossed the engine trade threshold">
            Z LIVE
          </span>
        )}
      </div>

      {/* Score */}
      <div className="text-muted text-[9px] mb-2">
        weighted score {score > 0 ? '+' : ''}{score} / {MAX_SCORE}
        {direction === 'SIDEWAYS' && ' · needs ±4 to trigger'}
      </div>

      {/* Vote dots — larger dot = higher weight */}
      <div className="flex gap-3">
        {SIGNALS.map(({ key, label, weight, title }) => {
          const v = votes[key] as 1 | 0 | -1
          const dotCls =
            v ===  1 ? 'bg-bull' :
            v === -1 ? 'bg-bear' :
            'bg-muted/30'
          const dotSize = weight === 2 ? 'w-3 h-3' : 'w-2 h-2'
          return (
            <div key={key} className="flex flex-col items-center gap-0.5" title={title}>
              <div className={clsx('rounded-full', dotCls, dotSize)} />
              <span className={clsx('text-[7px]', weight === 2 ? 'text-white/60' : 'text-muted')}>
                {label}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
