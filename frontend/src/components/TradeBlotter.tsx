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

function fmtDate(ts: number | null) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })
}

const EXIT_COLORS: Record<string, string> = {
  STOP_LOSS:    'text-bear',
  TAKE_PROFIT:  'text-bull',
  DIVERGENCE:   'text-accent',
  TIME_STOP:    'text-muted',
  KILL_SWITCH:  'text-warn',
}

function downloadCSV(trades: Trade[]) {
  const headers = [
    'ID', 'Date', 'Open Time', 'Close Time', 'Direction',
    'Symbol', 'Entry ₹', 'Exit ₹', 'Qty', 'Lot Size',
    'Capital at Risk ₹', 'Z-Score at Entry', 'Stop Loss ₹',
    'Exit Reason', 'P&L ₹', 'P&L %'
  ]

  const rows = trades
    .filter(t => t.closed_at !== null)
    .map(t => [
      t.id,
      fmtDate(t.opened_at),
      fmtTime(t.opened_at),
      fmtTime(t.closed_at),
      t.direction,
      t.symbol,
      t.entry_price,
      t.exit_price ?? '',
      t.qty,
      50,
      t.capital_at_risk,
      t.z_score_entry,
      t.stop_loss,
      t.exit_reason ?? '',
      t.pnl ?? '',
      t.pnl_pct !== null ? (t.pnl_pct * 100).toFixed(2) + '%' : '',
    ])

  const csv = [headers, ...rows]
    .map(row => row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))
    .join('\n')

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `market-sentinel-trades-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

export function TradeBlotter({ trades }: Props) {
  const closed = trades.filter(t => t.closed_at !== null)
  const totalPnl = closed.reduce((sum, t) => sum + (t.pnl ?? 0), 0)
  const wins = closed.filter(t => (t.pnl ?? 0) > 0).length

  return (
    <div className="flex flex-col gap-2">

      {/* Header row */}
      <div className="flex items-center justify-between">
        <h3 className="text-xs text-muted font-mono uppercase tracking-widest">
          Trade Blotter ({closed.length})
        </h3>
        <button
          onClick={() => downloadCSV(trades)}
          disabled={closed.length === 0}
          className="text-[10px] font-mono px-2 py-0.5 rounded border border-border
                     text-muted hover:text-white hover:border-accent transition-colors
                     disabled:opacity-30 disabled:cursor-not-allowed"
        >
          ↓ Download CSV
        </button>
      </div>

      {/* Summary bar */}
      {closed.length > 0 && (
        <div className="flex gap-3 text-[10px] font-mono text-muted border-b border-border pb-2">
          <span>
            Total P&L:{' '}
            <span className={clsx('font-semibold', totalPnl >= 0 ? 'text-bull' : 'text-bear')}>
              {totalPnl >= 0 ? '+' : ''}₹{fmt(totalPnl)}
            </span>
          </span>
          <span>
            Win rate:{' '}
            <span className="text-white">
              {wins}/{closed.length} ({closed.length > 0 ? Math.round(wins / closed.length * 100) : 0}%)
            </span>
          </span>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full font-mono text-xs border-collapse">
          <thead>
            <tr className="text-muted border-b border-border">
              <th className="text-left py-1 pr-2">ID</th>
              <th className="text-left pr-2">Open</th>
              <th className="text-left pr-2">Close</th>
              <th className="text-left pr-2">Dir</th>
              <th className="text-right pr-2">Entry</th>
              <th className="text-right pr-2">Exit</th>
              <th className="text-right pr-2">P&L ₹</th>
              <th className="text-right pr-2">P&L %</th>
              <th className="text-left">Reason</th>
            </tr>
          </thead>
          <tbody>
            {closed.length === 0 && (
              <tr>
                <td colSpan={9} className="text-muted italic py-4 text-center">
                  No closed trades yet
                </td>
              </tr>
            )}
            {closed.map(t => (
              <tr
                key={t.id}
                className="border-b border-border/50 hover:bg-panel transition-colors"
              >
                <td className="py-1 pr-2 text-muted">{t.id}</td>
                <td className="pr-2">{fmtTime(t.opened_at)}</td>
                <td className="pr-2">{fmtTime(t.closed_at)}</td>
                <td className={clsx('pr-2 font-semibold',
                  t.direction === 'CALL' ? 'text-bull' : 'text-bear')}>
                  {t.direction}
                </td>
                <td className="text-right pr-2">₹{fmt(t.entry_price)}</td>
                <td className="text-right pr-2">₹{fmt(t.exit_price)}</td>
                <td className={clsx('text-right pr-2 font-semibold',
                  (t.pnl ?? 0) >= 0 ? 'text-bull' : 'text-bear')}>
                  {(t.pnl ?? 0) >= 0 ? '+' : ''}₹{fmt(t.pnl)}
                </td>
                <td className={clsx('text-right pr-2',
                  (t.pnl_pct ?? 0) >= 0 ? 'text-bull' : 'text-bear')}>
                  {t.pnl_pct !== null ? `${(t.pnl_pct * 100).toFixed(1)}%` : '—'}
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
