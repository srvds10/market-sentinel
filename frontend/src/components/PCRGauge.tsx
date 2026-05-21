import clsx from 'clsx'

interface Props {
  pcr: number | null
  sentiment: string
  callOi: number
  putOi: number
}

const SENTIMENT_STYLE: Record<string, { label: string; cls: string }> = {
  PUT_HEAVY:  { label: 'PUT HEAVY',  cls: 'text-bear'  },
  CALL_HEAVY: { label: 'CALL HEAVY', cls: 'text-bull'  },
  BALANCED:   { label: 'BALANCED',   cls: 'text-muted' },
  WAIT:       { label: 'COLLECTING', cls: 'text-muted' },
}

function fmtOi(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export function PCRGauge({ pcr, sentiment, callOi, putOi }: Props) {
  const s = SENTIMENT_STYLE[sentiment] ?? SENTIMENT_STYLE.WAIT
  const total = callOi + putOi
  const callPct = total > 0 ? (callOi / total) * 100 : 50
  const putPct  = total > 0 ? (putOi  / total) * 100 : 50

  return (
    <div className="rounded-lg border border-border bg-panel p-3">

      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="text-muted text-[9px] uppercase tracking-wider">
          NIFTY PCR
        </div>
        <div className="flex items-center gap-2">
          {pcr !== null && (
            <span className="text-muted text-[9px]">
              PCR {pcr.toFixed(2)}
            </span>
          )}
          <span className={clsx('text-[10px] font-bold', s.cls)}>{s.label}</span>
        </div>
      </div>

      {/* Call / Put OI bar */}
      <div className="flex h-2 rounded-full overflow-hidden mb-2">
        <div
          className="bg-bull opacity-70 transition-all duration-500"
          style={{ width: `${callPct}%` }}
        />
        <div
          className="bg-bear opacity-70 transition-all duration-500"
          style={{ width: `${putPct}%` }}
        />
      </div>

      {/* OI labels */}
      <div className="flex justify-between text-[8px]">
        <div className="flex flex-col items-start">
          <span className="text-bull font-semibold">CALL OI</span>
          <span className="text-muted">{callOi > 0 ? fmtOi(callOi) : '—'}</span>
        </div>
        {pcr !== null && (
          <div className="flex flex-col items-center">
            <span className="text-muted">ratio</span>
            <span className={clsx('font-bold text-[10px]', s.cls)}>
              {pcr.toFixed(2)}
            </span>
          </div>
        )}
        <div className="flex flex-col items-end">
          <span className="text-bear font-semibold">PUT OI</span>
          <span className="text-muted">{putOi > 0 ? fmtOi(putOi) : '—'}</span>
        </div>
      </div>

      {total === 0 && (
        <div className="mt-1 text-[8px] text-muted text-center">
          Waiting for first OI poll…
        </div>
      )}
    </div>
  )
}
