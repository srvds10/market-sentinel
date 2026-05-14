export type EngineState = 'IDLE' | 'WARMING_UP' | 'ACTIVE' | 'KILLED' | 'CLOSED'

export type MarketBias = 'BULLISH' | 'BEARISH' | 'SIDEWAYS' | 'UNKNOWN'

export interface StatusPayload {
  engine_state: EngineState
  spot_ltp: number
  atm_ltp: number
  otm_call_ltp: number
  otm_put_ltp: number
  last_z_score: number | null
  last_ratio: number | null
  z_sample_count: number
  market_bias: MarketBias
  spot_delta_5m: number | null
  capital: number
  daily_pnl: number
  daily_pnl_pct: number
  open_trade: Trade | null
  last_signal: Signal | null
  warmup_remaining: number
  reconnect_count: number
  instrument_map: InstrumentMap | null
  server_time: number
  last_heartbeat: number
}

export interface Trade {
  id: string
  opened_at: number
  closed_at: number | null
  symbol: string
  direction: 'CALL' | 'PUT'
  entry_price: number
  exit_price: number | null
  qty: number
  capital_at_risk: number
  z_score_entry: number
  stop_loss: number
  exit_reason: string | null
  pnl: number | null
  pnl_pct: number | null
}

export interface Signal {
  timestamp: number
  z_score: number
  ratio: number
  direction: string
  spot_ltp: number
  atm_symbol: string
  acted_on?: boolean
}

export interface InstrumentMap {
  atm_strike: number
  otm_call_symbol: string
  otm_call_strike: number
  otm_call_delta: number
  otm_put_symbol: string
  otm_put_strike: number
  otm_put_delta: number
}

export type WSMessage =
  | ({ type: 'status' } & Partial<StatusPayload>)
  | ({ type: 'signal' } & Signal & { acted_on: boolean })
  | ({ type: 'trade_open' } & Trade)
  | ({ type: 'trade_close' } & Trade)
  | { type: 'ping' }
