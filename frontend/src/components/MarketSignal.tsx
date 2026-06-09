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

interface Vote {
  label: string
  value: 1 | 0 | -1
}

function computeVotes(p: Props): Vote[] {
  const z = p.zScore
  return [
    {
      label: 'Z',
      value: z === null ? 0 : z > 1.0 ? 1 : z < -1.0 ? -1 : 0,
    },
    {
      label: 'BIAS',
      value: p.marketBias === 'BULLISH' ? 1 : p.marketBias === 'BEARISH' ? -1 : 0,
    },
    {
      label: 'VWAP',
      value: p.aboveVwap === true ? 1 : p.aboveVwap === false ? -1 : 0,
    },
    {
      label: 'OPT',
      value: p.pressureVerdict === 'CALL_DOMINANT' ? 1
           : p.pressureVerdict === 'PUT_DOMINANT'  ? -1
           : 0,
    },
    {
      label: 'HW',
      value: p.hwDirection === 'BULLISH' ? 1 : p.hwDirection === 'BEARISH' ? -1 : 0,
    },
    {
      label: 'PCR',
      value: p.pcrStale ? 0
           : p.pcrSentiment === 'CALL_HEAVY' ? 1
           : p.pcrSentiment === 'PUT_HEAVY'  ? -1
           : 0,
    },
  ]
}

const VOTE_DOT: Record<1 | 0 | -1, string> = {
   1: 'bg-bull',
   0: 'bg-muted/30',
  '-1': 'bg-bear',
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
  const score = votes.reduce((s, v) => s + v.value, 0)
  const absScore = Math.abs(score)

  const direction: 'UP' | 'DOWN' | 'SIDEWAYS' =
    score >= 3 ? 'UP' : score <= -3 ? 'DOWN' : 'SIDEWAYS'

  const strength =
    absScore >= 5 ? 'STRONG' :
    absScore >= 4 ? 'HIGH'   :
    absScore >= 3 ? 'MOD'    : ''

  const bullCount = votes.filter(v => v.value ===  1).length
  const bearCount = votes.filter(v => v.value === -1).length
  const agreeCount = direction === 'UP' ? bullCount : direction === 'DOWN' ? bearCount : 0

  const dirCls =
    direction === 'UP'       ? 'text-bull' :
    direction === 'DOWN'     ? 'text-bear' :
    'text-muted'

  const dirLabel =
    direction === 'UP'       ? '▲ UP'       :
    direction === 'DOWN'     ? '▼ DOWN'     :
    '● SIDEWAYS'

  return (
    <div className="rounded-lg border border-border bg-panel p-3">
      <div className="text-muted text-[9px] uppercase tracking-wider mb-2">Market Signal</div>

      <div className="flex items-center justify-between mb-1">
        <span className={clsx('text-xl font-bold leading-none', dirCls)}>
          {dirLabel}
        </span>
        {strength && (
          <span className={clsx(
            'text-[9px] font-semibold px-1.5 py-0.5 rounded',
            direction === 'UP'   ? 'bg-bull/15 text-bull'  :
            direction === 'DOWN' ? 'bg-bear/15 text-bear'  :
            'bg-muted/10 text-muted',
          )}>
            {strength}
          </span>
        )}
      </div>

      <div className="text-muted text-[9px] mb-2">
        {direction !== 'SIDEWAYS'
          ? `${agreeCount}/6 agree · score ${score > 0 ? '+' : ''}${score}`
          : `${bullCount}↑ ${bearCount}↓ split · score ${score}`
        }
      </div>

      {/* Vote dots */}
      <div className="flex gap-2">
        {votes.map(({ label, value }) => (
          <div key={label} className="flex flex-col items-center gap-0.5">
            <div className={clsx('w-2.5 h-2.5 rounded-full', VOTE_DOT[value as 1 | 0 | -1])} />
            <span className="text-[7px] text-muted">{label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
