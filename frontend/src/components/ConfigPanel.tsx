import { useEffect, useState } from 'react'
import { fetchConfig, patchConfig } from '../hooks/useApi'

type ConfigMap = Record<string, Record<string, unknown>>

const EDITABLE: Array<{ section: string; key: string; label: string; type: 'number' | 'text' }> = [
  { section: 'signal',    key: 'zscore_threshold',              label: 'Z-Score threshold',       type: 'number' },
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
  const [cfg, setCfg] = useState<ConfigMap>({})
  const [saving, setSaving] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    fetchConfig().then(setCfg).catch(() => {})
  }, [])

  const handleChange = (section: string, key: string, raw: string) => {
    const field = EDITABLE.find(f => f.section === section && f.key === key)
    const value = field?.type === 'number' ? parseFloat(raw) : raw
    setCfg(prev => ({
      ...prev,
      [section]: { ...prev[section], [key]: value },
    }))
  }

  const handleSave = async (section: string, key: string) => {
    const value = cfg[section]?.[key]
    setSaving(`${section}.${key}`)
    try {
      await patchConfig(section, key, value)
      setMsg(`Saved ${section}.${key}`)
    } catch (e: unknown) {
      setMsg(`Error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(null)
      setTimeout(() => setMsg(null), 3000)
    }
  }

  return (
    <div className="font-mono text-xs">
      <h3 className="text-muted uppercase tracking-widest mb-3">Configuration</h3>
      {msg && (
        <div className="mb-3 px-2 py-1 rounded bg-accent/10 border border-accent/30 text-accent text-xs">
          {msg}
        </div>
      )}
      <div className="grid grid-cols-1 gap-2">
        {EDITABLE.map(({ section, key, label, type }) => {
          const val = String(cfg[section]?.[key] ?? '')
          const id = `${section}.${key}`
          return (
            <div key={id} className="flex items-center gap-2">
              <label className="w-48 text-muted shrink-0">{label}</label>
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
    </div>
  )
}
