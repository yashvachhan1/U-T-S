import { useState } from 'react'
import { cls, fmt, fmtInt, post, rs, sign } from '../api.js'
import Icon from '../components/Icon.jsx'
import InstrumentSearch, { PickedNote } from '../components/InstrumentSearch.jsx'
import StrategyPicker from '../components/StrategyPicker.jsx'
import { EquityChart, PriceChart } from '../components/charts.jsx'
import { useUI } from '../components/Feedback.jsx'
import { Check, Empty, Field, Num, Panel, Select, SideTag, Spinner, Stat, Table } from '../components/ui.jsx'

const TRADE_COLS = [
  { label: '#', num: true, get: (_, i) => <span className="muted">{i}</span> },
  { label: 'Dir', get: (t) => <SideTag side={t.direction === 'LONG' ? 'BUY' : 'SELL'} /> },
  { label: 'Entry', get: (t) => <span className="muted mono">{String(t.entry_time).slice(0, 10)}</span> },
  { label: 'Entry ₹', num: true, get: (t) => fmt(t.entry_price) },
  { label: 'Exit', get: (t) => <span className="muted mono">{String(t.exit_time || '').slice(0, 10)}</span> },
  { label: 'Exit ₹', num: true, get: (t) => fmt(t.exit_price) },
  { label: 'Qty', num: true, get: (t) => fmtInt(t.quantity) },
  { label: 'P&L', num: true, get: (t) => fmt(t.pnl), cls: (t) => cls(t.pnl) },
  { label: '%', num: true, get: (t) => `${sign(t.pnl_pct)}${fmt(t.pnl_pct)}`, cls: (t) => cls(t.pnl_pct) },
  { label: 'Exit reason', get: (t) => <span className="muted">{t.exit_reason}</span> },
]

export default function Backtest({ strategies, theme }) {
  const { toast } = useUI()

  const [instrument, setInstrument] = useState(null)
  const [strategy, setStrategy] = useState('')
  const [params, setParams] = useState({})
  const [cfg, setCfg] = useState({
    interval: '1d', days: 730, capital: 100000, position_pct: 100,
    stop_loss_pct: 0, target_pct: 0, brokerage: 20, slippage: 0.03,
  })
  const [allowShort, setAllowShort] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  if (!strategy && strategies.length) setStrategy(strategies[0].key)
  const set = (k) => (v) => setCfg((c) => ({ ...c, [k]: v }))

  const run = async () => {
    if (!instrument) return toast('Pehle instrument chunein', 'err')
    setBusy(true)
    try {
      setResult(await post('/backtest', {
        instrument_key: instrument.instrument_key,
        strategy,
        interval: cfg.interval,
        days: Number(cfg.days),
        params,
        capital: Number(cfg.capital),
        position_pct: Number(cfg.position_pct),
        stop_loss_pct: Number(cfg.stop_loss_pct),
        target_pct: Number(cfg.target_pct),
        allow_short: allowShort,
        brokerage_per_order: Number(cfg.brokerage),
        slippage_pct: Number(cfg.slippage),
      }))
    } catch (e) { toast(e.message, 'err') }
    setBusy(false)
  }

  const s = result?.stats
  const candles = result?.candles || []
  const buyHold = candles.length
    ? (candles[candles.length - 1].close / candles[0].close - 1) * 100 : 0
  const beat = s ? s.return_pct >= buyHold : false

  // Every entry and exit, drawn as an arrow on the price chart.
  const markers = (result?.trades || []).flatMap((t) => {
    const entrySide = t.direction === 'LONG' ? 'BUY' : 'SELL'
    const out = [{ time: t.entry_time, side: entrySide, text: `${entrySide} ${fmt(t.entry_price)}` }]
    if (t.exit_time) {
      out.push({
        time: t.exit_time,
        side: entrySide === 'BUY' ? 'SELL' : 'BUY',
        text: `${t.pnl >= 0 ? '+' : ''}${fmt(t.pnl, 0)}`,
      })
    }
    return out
  })

  return (
    <>
      <div className="page-head"><div><h1>Backtest</h1><p>Strategy ko purane data par test karein</p></div></div>

      <div className="split">
        <Panel title="Setup">
          <Field label="Instrument">
            <InstrumentSearch placeholder="Stock dhoondein…" onPick={setInstrument} />
          </Field>
          <PickedNote instrument={instrument} />

          <StrategyPicker
            strategies={strategies} value={strategy} onChange={setStrategy}
            params={params} onParams={setParams}
          />

          <div className="cols-2">
            <Select label="Interval" value={cfg.interval} onChange={set('interval')}
                    options={[['1d', '1 day'], ['1h', '1 hour'], ['30m', '30 min'], ['15m', '15 min'], ['5m', '5 min']]} />
            <Num label="Lookback days" value={cfg.days} onChange={set('days')} min="30" />
            <Num label="Capital ₹" value={cfg.capital} onChange={set('capital')} step="1000" />
            <Num label="Position %" value={cfg.position_pct} onChange={set('position_pct')} min="1" max="100" />
            <Num label="Stop loss %" value={cfg.stop_loss_pct} onChange={set('stop_loss_pct')} step="0.5" />
            <Num label="Target %" value={cfg.target_pct} onChange={set('target_pct')} step="0.5" />
            <Num label="Brokerage ₹" value={cfg.brokerage} onChange={set('brokerage')} step="1" />
            <Num label="Slippage %" value={cfg.slippage} onChange={set('slippage')} step="0.01" />
          </div>

          <Check checked={allowShort} onChange={setAllowShort} style={{ marginBottom: 12 }}>
            Short selling allow karein
          </Check>

          <button className="btn primary lg block" onClick={run} disabled={busy}>
            {busy ? <><Spinner />Chal raha hai…</> : <><Icon name="play" />Backtest chalayein</>}
          </button>
        </Panel>

        <div className="stack">
          <div className="stats">
            {!s
              ? <Empty icon="flask">Abhi koi backtest nahi chalaya</Empty>
              : (
                <>
                  <Stat label="Net P&L" value={rs(s.net_pnl)} accent={s.net_pnl >= 0 ? 'g' : 'r'}
                        colour={cls(s.net_pnl)} sub={`${sign(s.return_pct)}${fmt(s.return_pct)}%`} />
                  <Stat label="Buy & hold" value={`${sign(buyHold)}${fmt(buyHold)}%`}
                        accent={beat ? '' : 'r'} colour={cls(buyHold)}
                        sub={beat ? 'Strategy aage hai' : 'Strategy peeche hai'} />
                  <Stat label="CAGR" value={s.cagr_pct != null ? `${fmt(s.cagr_pct)}%` : '—'}
                        accent="a" colour={s.cagr_pct != null ? cls(s.cagr_pct) : ''} />
                  <Stat label="Win rate" value={`${fmt(s.win_rate)}%`} accent="a"
                        sub={`${s.wins}W / ${s.losses}L`} />
                  <Stat label="Trades" value={fmtInt(s.total_trades)} />
                  <Stat label="Profit factor" value={s.profit_factor != null ? fmt(s.profit_factor) : '—'}
                        accent={s.profit_factor >= 1.5 ? 'g' : ''} />
                  <Stat label="Max drawdown" value={`${fmt(s.max_drawdown_pct)}%`} accent="r" colour="down" />
                  <Stat label="Avg win" value={rs(s.avg_win)} accent="g" colour="up" />
                  <Stat label="Avg loss" value={rs(s.avg_loss)} accent="r" colour="down" />
                  <Stat label="Total costs" value={rs(s.total_costs)} colour="muted" />
                  <Stat label="Final capital" value={rs(s.final_capital)} accent="a" />
                  <Stat label="Best / worst"
                        value={`${fmt(s.best_trade, 0)} / ${fmt(s.worst_trade, 0)}`} />
                </>
              )}
          </div>

          <Panel
            title="Price & trades"
            right={<span className="muted" style={{ fontSize: 11 }}>
              {markers.length ? `${markers.length} markers` : ''}
            </span>}
            bodyStyle={{ padding: 8 }}
          >
            {result
              ? <PriceChart candles={candles} theme={theme} markers={markers} height={300} />
              : <Empty icon="chart">Backtest chalane par entry/exit yahan dikhenge</Empty>}
          </Panel>

          <Panel
            title="Equity curve"
            right={<span className="muted mono" style={{ fontSize: 11 }}>
              {candles.length
                ? `${String(candles[0].ts).slice(0, 10)} → ${String(candles[candles.length - 1].ts).slice(0, 10)}`
                : ''}
            </span>}
            bodyStyle={{ padding: 8 }}
          >
            <EquityChart equity={result?.equity} theme={theme} />
          </Panel>

          <Panel title="Trades" bodyClass="flush" bodyStyle={{ maxHeight: 360 }}>
            <Table cols={TRADE_COLS} rows={result?.trades}
                   empty={result ? 'Is strategy ne koi trade nahi liya' : 'Backtest chalayein'} />
          </Panel>
        </div>
      </div>
    </>
  )
}
