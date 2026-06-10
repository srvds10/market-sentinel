import clsx from 'clsx'
import type { Trade } from '../types'

interface Props {
  trade: Trade | null
  atm_call_ltp: number
  atm_put_ltp: number
}

function fmt(n: number) {
  return n.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('en-IN', { hour12: false })
}

export function PositionTracker({ trade, atm_call_ltp, atm_put_ltp }: Props) {
  if (!trade) {
    return (
      <div className="rounded-lg border border-border bg-panel p-4 flex items-center justify-center h-32">
        <p className="text-muted font-mono text-sm">No open position</p>
      </div>
    )
  }

  const current_ltp = trade.direction === 'CALL' ? atm_call_ltp : atm_put_ltp
  // Derive lot_size from what was recorded at entry rather than hardcoding 50
  const lot_size = (trade.entry_price > 0 && trade.qty > 0 && trade.capital_at_risk > 0)
    ? Math.round(trade.capital_at_risk / (trade.entry_price * trade.qty))
    : 50
  const unreal_pnl = (current_ltp - trade.entry_price) * trade.qty * lot_size
  const unreal_pct = trade.entry_price > 0
    ? (current_ltp - trade.entry_price) / trade.entry_price
    : 0

  return (
    <div className={clsx(
      'rounded-lg border p-4 font-mono text-sm',
      unreal_pnl >= 0 ? 'border-bull/40 bg-bull/5' : 'border-bear/40 bg-bear/5',
    )}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-muted uppercase tracking-wider">Open Position</span>
        <span className={clsx(
          'px-2 py-0.5 rounded text-xs font-semibold',
          trade.direction === 'CALL' ? 'bg-bull/20 text-bull' : 'bg-bear/20 text-bear',
        )}>
          {trade.direction}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <div className="text-muted">Symbol</div>
        <div className="text-right text-white">{trade.symbol}</div>

        <div className="text-muted">Entry</div>
        <div className="text-right">₹{fmt(trade.entry_price)}</div>

        <div className="text-muted">Current</div>
        <div className="text-right">₹{fmt(current_ltp)}</div>

        <div className="text-muted">Qty × Lot</div>
        <div className="text-right">{trade.qty} × {lot_size} = {trade.qty * lot_size}</div>

        <div className="text-muted">Stop Loss</div>
        <div className="text-right text-bear">₹{fmt(trade.stop_loss)}</div>

        <div className="text-muted">Take Profit</div>
        <div className="text-right text-bull">₹{fmt(trade.take_profit)}</div>

        <div className="text-muted">Z at entry</div>
        <div className="text-right text-warn">{trade.z_score_entry.toFixed(3)}</div>

        <div className="text-muted">Opened</div>
        <div className="text-right">{fmtTime(trade.opened_at)}</div>
      </div>

      <div className="mt-3 pt-3 border-t border-border flex items-center justify-between">
        <span className="text-muted text-xs">Unrealised P&L</span>
        <span className={clsx(
          'font-semibold text-base',
          unreal_pnl >= 0 ? 'text-bull' : 'text-bear',
        )}>
          {unreal_pnl >= 0 ? '+' : ''}₹{fmt(unreal_pnl)}
          <span className="text-xs ml-1 opacity-70">
            ({(unreal_pct * 100).toFixed(1)}%)
          </span>
        </span>
      </div>
    </div>
  )
}
