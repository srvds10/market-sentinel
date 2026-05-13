import { useEffect, useState } from 'react'
import { fetchConfig, patchConfig, updateToken } from '../hooks/useApi'

type ConfigMap = Record<string, Record<string, unknown>>

const EDITABLE: Array<{ section: string; key: string; label: string; type: 'number' | 'text' }> = [
  { section: 'execution', key: 'virtual_capital',               label: 'Virtual capital (₹)',     type: 'number' },
  { section: 'signal',    key: 'zscore_threshold',              label: 'Z-Score threshold',        type: 'number' },
  { section: 'signal',    key: 'min_spot_delta',                label: 'Min spot Δ (points)',      type: 'number' },
  { section: 'signal',    key: 'zscore_lookback_minutes',       label: 'Z lookback (min)',         type: 'number' },
  { section: 'execution', key: 'stop_loss_pct',                 label: 'Stop loss %',             type: 'number' },
  { section: 'execution', key: 'trailing_stop_activation_pct',  label: 'Trail activation %',      type: 'number' },
  { section: 'execution', key: 'trailing_stop_pct',             label: 'Trail floor %',           type: 'number' },
  { section: 'execution', key: 'cooldown_minutes',              label: 'Cooldown (min)',           type: 'number' },
  { section: 'execution', key: 'daily_drawdown_kill_pct',       label: 'Kill switch drawdown %',  type: 'number' },
  { section: 'execution', key: 'morning_filter_start',          label: 'Morning start (HH:MM)',   type: 'text'   },
  { section: 'execution', key: 'morning_filter_end',            label: 'Morning end (HH:MM)',     type: 'text'   },
]

export function ConfigPanel() {
  const [cfg, setCfg]         = useState<ConfigMap>({})
  const [saving, setSaving]   = useState<string | null>(null)
  const [msg, setMsg]         = useState<{ text: string; ok: boolean } | null>(null)
  const [token, setToken]     = useState('')
  const [showToken, setShowToken] = useState(false)
  const [savingToken, setSavingToken] = useState(false)

  useEffect(() => {
    fetchConfig().then(setCfg).catch(() => {})
  }, [])

  const flash = (text: string, ok: boolean) => {
    setMsg({ text, ok })
    setTimeout(() => setMsg(null), 3500)
  }

  const handleChange = (section: string, key: string, raw: string) => {
    const field = EDITABLE.find(f => f.section === section && f.key === key)
    const value = field?.type === 'number' ? parseFloat(raw) : raw
    setCfg(prev => ({ ...prev, [section]: { ...prev[section], [key]: value } }))
  }

  const handleSave = async (section: string, key: string) => {
    const value = cfg[section]?.[key]
    setSaving(`${section}.${key}`)
    try {
      await patchConfig(section, key, value)
      flash(`✓ Saved — ${label(section, key)}`, true)
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
      flash('✓ Token updated — engine reconnecting', true)
      setToken('')
    } catch (e: unknown) {
      flash(`✗ ${e instanceof Error ? e.message : String(e)}`, false)
    } finally {
      setSavingToken(false)
    }
  }

  const label = (section: string, key: string) =>
    EDITABLE.find(f => f.section === section && f.key === key)?.label ?? key

  return (
    <div className="font-mono text-xs flex flex-col gap-4">

      {/* ── Dhan API Token ── */}
      <div className="rounded border border-warn/30 bg-warn/5 p-3 flex flex-col gap-2">
        <div className="text-warn uppercase tracking-wider text-[10px]">Dhan API Token (changes daily)</div>
        <div className="text-muted text-[10px]">Client ID: <span className="text-white">1102982629</span> (fixed)</div>
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
            className="shrink-0 px-2 py-1 rounded border border-border text-muted hover:text-white transition-colors"
          >
            {showToken ? 'Hide' : 'Show'}
          </button>
          <button
            onClick={handleTokenSave}
            disabled={savingToken || !token.trim()}
            className="shrink-0 px-3 py-1 rounded bg-warn/20 hover:bg-warn/40
                       border border-warn/30 text-warn transition-colors disabled:opacity-40"
          >
            {savingToken ? '…' : 'Update'}
          </button>
        </div>
      </div>

      {/* ── Flash message ── */}
      {msg && (
        <div className={`px-2 py-1 rounded border text-xs ${
          msg.ok
            ? 'bg-bull/10 border-bull/30 text-bull'
            : 'bg-bear/10 border-bear/30 text-bear'
        }`}>
          {msg.text}
        </div>
      )}

      {/* ── Config fields ── */}
      <div className="flex flex-col gap-2">
        <div className="text-muted uppercase tracking-wider text-[10px]">Engine Parameters</div>
        {EDITABLE.map(({ section, key, label, type }) => {
          const val = String(cfg[section]?.[key] ?? '')
          const id = `${section}.${key}`
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
                           border border-accent/30 text-accent transition-colors disabled:opacity-50"
              >
                {saving === id ? '…' : 'Save'}
              </button>
            </div>
          )
        })}
      </div>

      <p className="text-muted text-[10px] leading-relaxed">
        Changes save to config.yaml instantly. Virtual capital takes effect on next day reset (09:15).
        The engine runs 24/7 — closing the browser does not stop it.
      </p>
    </div>
  )
}
