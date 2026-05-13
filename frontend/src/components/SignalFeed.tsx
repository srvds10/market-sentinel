import clsx from 'clsx'
import type { Signal } from '../types'

interface Props {
  signals: Signal[]
}

function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('en-IN', { hour12: false })
}

export function SignalFeed({ signals }: Props) {
  return (
    <div className="flex flex-col gap-1">
      <h3 className="text-xs text-muted font-mono uppercase tracking-widest mb-1">
        Signal History
      </h3>
      {signals.length === 0 && (
        <p className="text-muted text-xs font-mono italic">No signals yet</p>
      )}
      <div className="flex flex-col gap-1 max-h-64 overflow-y-auto pr-1 scrollbar-thin">
        {signals.map((s, i) => (
          <div
            key={i}
            className={clsx(
              'flex items-center gap-2 rounded px-2 py-1 font-mono text-xs border',
              s.acted_on
                ? 'bg-accent/10 border-accent/30'
                : 'bg-panel border-border',
            )}
          >
            <span className="text-muted w-16 shrink-0">{fmtTime(s.timestamp)}</span>
            <span
              className={clsx(
                'w-10 text-center font-semibold rounded px-1',
                s.direction === 'CALL' ? 'text-bull bg-bull/10' : 'text-bear bg-bear/10',
              )}
            >
              {s.direction}
            </span>
            <span className="text-warn w-12">Z={s.z_score.toFixed(2)}</span>
            <span className="text-muted">r={s.ratio.toFixed(3)}</span>
            <span className="text-muted ml-auto">
              ₹{s.spot_ltp.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
            </span>
            {s.acted_on && (
              <span className="text-accent text-[10px] border border-accent/40 rounded px-1">
                TRADED
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
