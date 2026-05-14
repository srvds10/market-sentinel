import clsx from 'clsx'
import type { MarketBias as MarketBiasType } from '../types'

interface Props {
  bias: MarketBiasType
  delta5m: number | null
}

const CONFIG: Record<MarketBiasType, { icon: string; color: string; label: string }> = {
  BULLISH:  { icon: '▲', color: 'text-bull',  label: 'Bullish'  },
  BEARISH:  { icon: '▼', color: 'text-bear',  label: 'Bearish'  },
  SIDEWAYS: { icon: '▶', color: 'text-warn',  label: 'Sideways' },
  UNKNOWN:  { icon: '◆', color: 'text-muted', label: 'Unknown'  },
}

export function MarketBias({ bias, delta5m }: Props) {
  const { icon, color, label } = CONFIG[bias] ?? CONFIG.UNKNOWN
  const deltaStr = delta5m !== null
    ? `${delta5m >= 0 ? '+' : ''}${delta5m.toFixed(1)} pts`
    : '—'

  return (
    <div className="rounded-lg border border-border bg-panel p-3">
      <div className="text-muted text-[9px] uppercase tracking-wider mb-2">Market Bias</div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={clsx('text-xl font-bold', color)}>{icon}</span>
          <span className={clsx('font-bold text-base tracking-wide', color)}>{label}</span>
        </div>
        <div className="text-right">
          <div className="text-muted text-[9px]">5-min Δ</div>
          <div className={clsx('font-mono text-xs font-semibold', color)}>{deltaStr}</div>
        </div>
      </div>
    </div>
  )
}
