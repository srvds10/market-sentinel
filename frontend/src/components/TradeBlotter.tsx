import clsx from 'clsx'
import type { Trade } from '../types'

interface Props {
  trades: Trade[]
}

function fmt(n: number | null) {
  if (n === null) return '—'
  return n.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function fmtTime(ts: number | null) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleTimeString('en-IN', { hour12: false })
}

const EXIT_COLORS: Record<string, string> = {
  STOP_LOSS:      'text-bear',
  TRAILING_STOP:  'text-bull',
  DIVERGENCE:     'text-accent',
  TIME_STOP:      'text-muted',
  KILL_SWITCH:    'text-warn',
}

export function TradeBlotter({ trades }: Props) {
  const closed = trades.filter(t => t.closed_at !== null)

  return (
    <div>
      <h3 className="text-xs text-muted font-mono uppercase tracking-widest mb-2">
        Trade Blotter ({closed.length})
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full font-mono text-xs border-collapse">
          <thead>
            <tr className="text-muted border-b border-border">
              <th className="text-left py-1 pr-3">ID</th>
              <th className="text-left pr-3">Open</th>
              <th className="text-left pr-3">Close</th>
              <th className="text-left pr-3">Dir</th>
              <th className="text-right pr-3">Entry</th>
              <th className="text-right pr-3">Exit</th>
              <th className="text-right pr-3">P&L ₹</th>
              <th className="text-right pr-3">P&L %</th>
              <th className="text-left">Reason</th>
            </tr>
          </thead>
          <tbody>
            {closed.length === 0 && (
              <tr>
                <td colSpan={9} className="text-muted italic py-3 text-center">
                  No closed trades yet
                </td>
              </tr>
            )}
            {closed.map(t => (
              <tr
                key={t.id}
                className={clsx(
                  'border-b border-border/50 hover:bg-panel transition-colors',
                  t.pnl !== null && t.pnl >= 0 ? 'text-white' : 'text-white/80',
                )}
              >
                <td className="py-1 pr-3 text-muted">{t.id}</td>
                <td className="pr-3">{fmtTime(t.opened_at)}</td>
                <td className="pr-3">{fmtTime(t.closed_at)}</td>
                <td className={clsx('pr-3 font-semibold',
                  t.direction === 'CALL' ? 'text-bull' : 'text-bear')}>
                  {t.direction}
                </td>
                <td className="text-right pr-3">₹{fmt(t.entry_price)}</td>
                <td className="text-right pr-3">₹{fmt(t.exit_price)}</td>
                <td className={clsx('text-right pr-3 font-semibold',
                  (t.pnl ?? 0) >= 0 ? 'text-bull' : 'text-bear')}>
                  {(t.pnl ?? 0) >= 0 ? '+' : ''}₹{fmt(t.pnl)}
                </td>
                <td className={clsx('text-right pr-3',
                  (t.pnl_pct ?? 0) >= 0 ? 'text-bull' : 'text-bear')}>
                  {t.pnl_pct !== null
                    ? `${(t.pnl_pct * 100).toFixed(1)}%`
                    : '—'}
                </td>
                <td className={EXIT_COLORS[t.exit_reason ?? ''] ?? 'text-muted'}>
                  {t.exit_reason ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
