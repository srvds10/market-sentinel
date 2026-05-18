import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { fetchConfig, patchConfig, updateToken, downloadLogs, clearLogs, fetchLogSize, clearRecords } from '../hooks/useApi'

type ConfigMap = Record<string, Record<string, unknown>>

// scale: multiply raw config value by this for display; divide on save.
// Percentage fields are stored as decimals (0.30) but shown as whole numbers (30).
const EDITABLE: Array<{ section: string; key: string; label: string; type: 'number' | 'text'; scale?: number }> = [
  { section: 'execution', key: 'virtual_capital',               label: 'Virtual capital (₹)',     type: 'number' },
  { section: 'signal',    key: 'zscore_threshold',              label: 'Z-Score threshold',        type: 'number' },
  { section: 'signal',    key: 'min_spot_delta',                label: 'Min spot Δ (points)',      type: 'number' },
  { section: 'signal',    key: 'zscore_lookback_minutes',       label: 'Z lookback (min)',         type: 'number' },
  { section: 'execution', key: 'stop_loss_pct',                 label: 'Stop loss %',             type: 'number', scale: 100 },
  { section: 'execution', key: 'trailing_stop_activation_pct',  label: 'Trail activation %',      type: 'number', scale: 100 },
  { section: 'execution', key: 'trailing_stop_pct',             label: 'Trail floor %',           type: 'number', scale: 100 },
  { section: 'execution', key: 'cooldown_minutes',              label: 'Cooldown (min)',           type: 'number' },
  { section: 'execution', key: 'daily_drawdown_kill_pct',       label: 'Kill switch drawdown %',  type: 'number', scale: 100 },
  { section: 'execution', key: 'last_entry_time',               label: 'Last entry time (HH:MM)', type: 'text'   },
]

export function ConfigPanel() {
  const [cfg, setCfg]             = useState<ConfigMap>({})
  const [saving, setSaving]       = useState<string | null>(null)
  const [msg, setMsg]             = useState<{ text: string; ok: boolean } | null>(null)
  const [token, setToken]             = useState('')
  const [showToken, setShowToken]     = useState(false)
  const [savingToken, setSavingToken] = useState(false)
  const [logSize, setLogSize]         = useState<number | null>(null)
  const [logBusy, setLogBusy]         = useState(false)
  const [recBusy, setRecBusy]         = useState(false)

  useEffect(() => {
    fetchConfig().then(setCfg).catch(() => {})
    fetchLogSize().then(r => setLogSize(r.bytes)).catch(() => {})
  }, [])

  const flash = (text: string, ok: boolean) => {
    setMsg({ text, ok })
    setTimeout(() => setMsg(null), 3500)
  }

  const handleChange = (section: string, key: string, raw: string) => {
    const field = EDITABLE.find(f => f.section === section && f.key === key)
    // Store display value as-is; scale is applied only on save
    const value = field?.type === 'number' ? parseFloat(raw) : raw
    setCfg(prev => ({ ...prev, [section]: { ...prev[section], [key]: value } }))
  }

  const handleSave = async (section: string, key: string) => {
    const field = EDITABLE.find(f => f.section === section && f.key === key)
    const displayValue = cfg[section]?.[key]
    // Convert display value back to raw config (e.g. 30 → 0.30 for pct fields)
    const value = (field?.scale && typeof displayValue === 'number')
      ? displayValue / field.scale
      : displayValue
    setSaving(`${section}.${key}`)
    try {
      await patchConfig(section, key, value)
      flash(`✓ Saved`, true)
    } catch (e: unknown) {
      flash(`✗ ${e instanceof Error ? e.message : String(e)}`, false)
    } finally {
      setSaving(null)
    }
  }

  const handleTokenSave = async () => {
    if (!token.trim()) return
    setSavingToken(true)
    try {
      await updateToken(token.trim())
      flash('✓ Token updated — engine reconnecting with new credentials', true)
      setToken('')
    } catch (e: unknown) {
      flash(`✗ ${e instanceof Error ? e.message : String(e)}`, false)
    } finally {
      setSavingToken(false)
    }
  }

  const fmtSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
  }

  const handleDownloadLog = async () => {
    setLogBusy(true)
    try {
      await downloadLogs()
    } catch (e: unknown) {
      flash(`✗ ${e instanceof Error ? e.message : String(e)}`, false)
    } finally {
      setLogBusy(false)
    }
  }

  const handleClearLog = async () => {
    if (!confirm('Clear the entire log file? This cannot be undone.')) return
    setLogBusy(true)
    try {
      await clearLogs()
      setLogSize(0)
      flash('✓ Log file cleared', true)
    } catch (e: unknown) {
      flash(`✗ ${e instanceof Error ? e.message : String(e)}`, false)
    } finally {
      setLogBusy(false)
    }
  }

  return (
    <div className="font-mono text-xs flex flex-col gap-4">

      {/* ── Dhan API Token ── */}
      <div className="rounded border border-warn/30 bg-warn/5 p-3 flex flex-col gap-2">
        <div className="text-warn uppercase tracking-wider text-[10px]">Dhan API Token (changes daily)</div>
        <div className="text-muted text-[10px]">
          Client ID: <span className="text-white">1102982629</span> (fixed)
        </div>
        <div className="flex items-center gap-2">
          <input
            type={showToken ? 'text' : 'password'}
            value={token}
            onChange={e => setToken(e.target.value)}
            placeholder="Paste new access token here"
            className="flex-1 bg-surface border border-border rounded px-2 py-1 text-white
                       focus:outline-none focus:border-warn min-w-0 placeholder:text-muted/50"
          />
          <button
            onClick={() => setShowToken(v => !v)}
            className="shrink-0 px-2 py-1 rounded border border-border text-muted hover:text-white"
          >
            {showToken ? 'Hide' : 'Show'}
          </button>
          <button
            onClick={handleTokenSave}
            disabled={savingToken || !token.trim()}
            className="shrink-0 px-3 py-1 rounded bg-warn/20 hover:bg-warn/40
                       border border-warn/30 text-warn disabled:opacity-40"
          >
            {savingToken ? '…' : 'Update'}
          </button>
        </div>
      </div>

      {/* ── Flash message ── */}
      {msg && (
        <div className={clsx('px-2 py-1 rounded border text-xs', msg.ok
          ? 'bg-bull/10 border-bull/30 text-bull'
          : 'bg-bear/10 border-bear/30 text-bear'
        )}>
          {msg.text}
        </div>
      )}

      {/* ── Config fields ── */}
      <div className="flex flex-col gap-2">
        <div className="text-muted uppercase tracking-wider text-[10px]">Engine Parameters</div>
        {EDITABLE.map(({ section, key, label, type, scale }) => {
          const raw = cfg[section]?.[key]
          const displayed = (scale && typeof raw === 'number') ? raw * scale : raw
          const val = String(displayed ?? '')
          const id  = `${section}.${key}`
          return (
            <div key={id} className="flex items-center gap-2">
              <label className="w-44 text-muted shrink-0 leading-tight">{label}</label>
              <input
                type={type === 'number' ? 'number' : 'text'}
                value={val}
                step={type === 'number' ? 'any' : undefined}
                onChange={e => handleChange(section, key, e.target.value)}
                className="flex-1 bg-surface border border-border rounded px-2 py-0.5 text-white
                           focus:outline-none focus:border-accent min-w-0"
              />
              <button
                onClick={() => handleSave(section, key)}
                disabled={saving === id}
                className="shrink-0 px-2 py-0.5 rounded bg-accent/20 hover:bg-accent/40
                           border border-accent/30 text-accent disabled:opacity-50"
              >
                {saving === id ? '…' : 'Save'}
              </button>
            </div>
          )
        })}
      </div>

      <p className="text-muted text-[10px] leading-relaxed">
        Changes save instantly. Virtual capital takes effect on next day reset (09:15).
        Closing the browser does NOT stop the engine — it runs 24/7 on the server.
      </p>

      {/* ── Clear Records ── */}
      <div className="rounded border border-bear/30 bg-bear/5 p-3 flex flex-col gap-2">
        <div className="text-bear uppercase tracking-wider text-[10px]">Clear Database Records</div>
        <p className="text-muted text-[10px] leading-relaxed">
          Permanently deletes trades and/or signals from the database. Cannot be undone.
        </p>
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={async () => {
              if (!confirm('Delete ALL trade records? This cannot be undone.')) return
              setRecBusy(true)
              try {
                const r = await clearRecords({ trades: true, signals: false })
                flash(`✓ Cleared ${r.deleted.trades ?? 0} trade(s)`, true)
              } catch (e: unknown) {
                flash(`✗ ${e instanceof Error ? e.message : String(e)}`, false)
              } finally { setRecBusy(false) }
            }}
            disabled={recBusy}
            className="px-2 py-1 rounded bg-bear/20 hover:bg-bear/40 border border-bear/30 text-bear disabled:opacity-40"
          >
            {recBusy ? '…' : 'Clear Trades'}
          </button>
          <button
            onClick={async () => {
              if (!confirm('Delete ALL signal records? This cannot be undone.')) return
              setRecBusy(true)
              try {
                const r = await clearRecords({ trades: false, signals: true })
                flash(`✓ Cleared ${r.deleted.signals ?? 0} signal(s)`, true)
              } catch (e: unknown) {
                flash(`✗ ${e instanceof Error ? e.message : String(e)}`, false)
              } finally { setRecBusy(false) }
            }}
            disabled={recBusy}
            className="px-2 py-1 rounded bg-bear/20 hover:bg-bear/40 border border-bear/30 text-bear disabled:opacity-40"
          >
            {recBusy ? '…' : 'Clear Signals'}
          </button>
          <button
            onClick={async () => {
              if (!confirm('Delete ALL trades AND signals? This cannot be undone.')) return
              setRecBusy(true)
              try {
                const r = await clearRecords({ trades: true, signals: true })
                flash(`✓ Cleared ${r.deleted.trades ?? 0} trade(s) + ${r.deleted.signals ?? 0} signal(s)`, true)
              } catch (e: unknown) {
                flash(`✗ ${e instanceof Error ? e.message : String(e)}`, false)
              } finally { setRecBusy(false) }
            }}
            disabled={recBusy}
            className="px-2 py-1 rounded bg-bear/30 hover:bg-bear/50 border border-bear/40 text-bear font-semibold disabled:opacity-40"
          >
            {recBusy ? '…' : 'Clear All'}
          </button>
        </div>
      </div>

      {/* ── Error Logs ── */}
      <div className="rounded border border-border bg-surface p-3 flex flex-col gap-2">
        <div className="text-muted uppercase tracking-wider text-[10px]">Error Logs</div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted text-[10px]">
            {logSize === null ? 'Checking…' : logSize === 0 ? 'Log is empty' : `File size: ${fmtSize(logSize)}`}
          </span>
          <div className="flex gap-2">
            <button
              onClick={handleDownloadLog}
              disabled={logBusy || logSize === 0}
              className="px-2 py-1 rounded bg-accent/20 hover:bg-accent/40
                         border border-accent/30 text-accent disabled:opacity-40"
            >
              {logBusy ? '…' : 'Download'}
            </button>
            <button
              onClick={handleClearLog}
              disabled={logBusy || logSize === 0}
              className="px-2 py-1 rounded bg-bear/20 hover:bg-bear/40
                         border border-bear/30 text-bear disabled:opacity-40"
            >
              Clear
            </button>
          </div>
        </div>
        <p className="text-muted text-[10px] leading-relaxed">
          Rotating log — max 5 MB, 3 backups kept. Download to inspect Dhan
          connection errors, signal activity, and engine events.
        </p>
      </div>
    </div>
  )
}
