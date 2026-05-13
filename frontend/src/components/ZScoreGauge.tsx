import { PieChart, Pie, Cell } from 'recharts'

interface Props {
  zScore: number | null
  threshold: number
  sampleCount: number
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

export function ZScoreGauge({ zScore, threshold, sampleCount }: Props) {
  const MAX_Z = 4
  const z = zScore ?? 0
  const pct = clamp((z + MAX_Z) / (2 * MAX_Z), 0, 1)   // 0 → 1 over [-4, +4]

  // Arc goes 210° → 330° (240° sweep starting at 7 o'clock)
  const START_ANGLE = 210
  const END_ANGLE   = -30

  const filled = [
    { value: pct * 100 },
    { value: (1 - pct) * 100 },
  ]

  const zColor = z >= threshold ? '#22c55e' : z >= threshold * 0.7 ? '#f59e0b' : '#6366f1'
  const isReady = sampleCount >= 30

  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative w-36 h-24">
        <PieChart width={144} height={96}>
          {/* Background track */}
          <Pie
            data={[{ value: 100 }]}
            cx={72} cy={80}
            startAngle={START_ANGLE} endAngle={END_ANGLE}
            innerRadius={52} outerRadius={68}
            dataKey="value"
            stroke="none"
          >
            <Cell fill="#2a2d3a" />
          </Pie>
          {/* Filled arc */}
          <Pie
            data={filled}
            cx={72} cy={80}
            startAngle={START_ANGLE} endAngle={END_ANGLE}
            innerRadius={52} outerRadius={68}
            dataKey="value"
            stroke="none"
          >
            <Cell fill={zColor} />
            <Cell fill="transparent" />
          </Pie>
        </PieChart>

        {/* Centre readout */}
        <div className="absolute inset-0 flex flex-col items-center justify-end pb-1">
          <span className="font-mono text-xl font-semibold" style={{ color: zColor }}>
            {isReady ? (zScore !== null ? zScore.toFixed(2) : '—') : '…'}
          </span>
          <span className="text-muted text-[10px]">Z-SCORE</span>
        </div>
      </div>

      {/* Threshold marker label */}
      <div className="text-[11px] text-muted font-mono">
        threshold <span className="text-warn">+{threshold.toFixed(1)}σ</span>
        {' '}
        <span className="text-border">|</span>
        {' '}
        <span className={isReady ? 'text-bull' : 'text-warn'}>
          {sampleCount} samples
        </span>
      </div>
    </div>
  )
}
