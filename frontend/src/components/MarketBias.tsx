import clsx from 'clsx'
import type { MarketBias as MarketBiasType } from '../types'

interface Props {
  bias: MarketBiasType
  triggerDelta: number | null   // 1-min OTM premium change that set the bias
}

const CONFIG: Record<MarketBiasType, { icon: string; color: string; label: string; leg: string }> = {
  BULLISH:  { icon: '▲', color: 'text-bull',  label: 'Bullish',  leg: 'OTM CE' },
  BEARISH:  { icon: '▼', color: 'text-bear',  label: 'Bearish',  leg: 'OTM PE' },
  SIDEWAYS: { icon: '▶', color: 'text-warn',  label: 'Sideways', leg: 'CE↓ PE↓' },
  UNKNOWN:  { icon: '◆', color: 'text-muted', label: 'Unknown',  leg: '' },
}

export function MarketBias({ bias, triggerDelta }: Props) {
  const { icon, color, label, leg } = CONFIG[bias] ?? CONFIG.UNKNOWN

  const detail = triggerDelta !== null
    ? `${triggerDelta >= 0 ? '+' : ''}${triggerDelta.toFixed(2)} ${leg}`
    : leg || '—'

  return (
    <div className="rounded-lg border border-border bg-panel p-3">
      <div className="text-muted text-[9px] uppercase tracking-wider mb-2">Market Bias (1-min)</div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={clsx('text-xl font-bold', color)}>{icon}</span>
          <span className={clsx('font-bold text-base tracking-wide', color)}>{label}</span>
        </div>
        <div className="text-right">
          <div className="text-muted text-[9px]">OTM flow</div>
          <div className={clsx('font-mono text-xs font-semibold', color)}>{detail}</div>
        </div>
      </div>
    </div>
  )
}
