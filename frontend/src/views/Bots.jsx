import { useCallback, useEffect, useState } from 'react'
import { del, fmt, get, post } from '../api.js'
import Icon from '../components/Icon.jsx'
import InstrumentSearch, { PickedNote } from '../components/InstrumentSearch.jsx'
import StrategyPicker from '../components/StrategyPicker.jsx'
import { useUI } from '../components/Feedback.jsx'
import { useInterval } from '../hooks/index.js'
import { Check, Empty, Field, Note, Num, Panel, Pill, Select } from '../components/ui.jsx'

export default function Bots({ strategies, status }) {
  const { toast, confirm } = useUI()

  const [instrument, setInstrument] = useState(null)
  const [strategy, setStrategy] = useState('')
  const [params, setParams] = useState({})
  const [cfg, setCfg] = useState({
    interval: '5m', quantity: 1, product: 'I',
    max_trades_per_day: 10, stop_loss_pct: 1.5, target_pct: 3,
  })
  const [squareOff, setSquareOff] = useState(true)
  const [marketHours, setMarketHours] = useState(true)

  const [active, setActive] = useState([])
  const [selected, setSelected] = useState(null)
  const [log, setLog] = useState('Bot select karein log dekhne ke liye.')

  if (!strategy && strategies.length) setStrategy(strategies[0].key)
  const set = (k) => (v) => setCfg((c) => ({ ...c, [k]: v }))

  const loadBots = useCallback(async () => {
    try { setActive((await get('/bots')).active) } catch { /* signed out */ }
  }, [])

  const loadLog = useCallback(async (runId) => {
    if (!runId) return
    try {
      const rows = await get(`/bots/${runId}/logs?limit=150`)
      setLog(rows.length ? rows : 'Abhi koi log nahi.')
    } catch (e) { setLog(e.message) }
  }, [])

  useEffect(() => { loadBots() }, [loadBots])
  useEffect(() => { loadLog(selected) }, [selected, loadLog])
  useInterval(() => { loadBots(); loadLog(selected) }, 5000)

  const start = async () => {
    if (!instrument) return toast('Pehle instrument chunein', 'err')
    const live = !status.paper_trading
    const strat = strategies.find((s) => s.key === strategy)
    const ok = await confirm({
      title: live ? 'LIVE BOT start karein' : 'Paper bot start karein',
      danger: live,
      okText: 'Start',
      rows: [
        ['Symbol', instrument.symbol],
        ['Strategy', strat ? strat.name : strategy],
        ['Interval', cfg.interval],
        ['Quantity', String(cfg.quantity)],
        ['Stop loss', cfg.stop_loss_pct + '%'],
        ['Target', cfg.target_pct + '%'],
      ],
      note: live
        ? 'Ye bot asli paise se khud orders dalega.'
        : 'Paper mode — koi order Upstox tak nahi jayega.',
    })
    if (!ok) return
    try {
      await post('/bots', {
        strategy,
        instrument_key: instrument.instrument_key,
        interval: cfg.interval,
        quantity: Number(cfg.quantity),
        product: cfg.product,
        params,
        stop_loss_pct: Number(cfg.stop_loss_pct),
        target_pct: Number(cfg.target_pct),
        max_trades_per_day: Number(cfg.max_trades_per_day),
        square_off_at_close: squareOff,
        only_market_hours: marketHours,
      })
      toast('Bot shuru ho gaya', 'ok')
      loadBots()
    } catch (e) { toast(e.message, 'err') }
  }

  const stop = async (runId) => {
    const ok = await confirm({
      title: 'Bot band karein?', danger: true, okText: 'Stop',
      note: 'Open position apne aap close nahi hogi — usse manually square off karein.',
    })
    if (!ok) return
    await del('/bots/' + runId)
    toast('Bot band ho gaya')
    loadBots()
  }

  return (
    <>
      <div className="page-head"><div><h1>Algo Bots</h1><p>Strategy ko live chalayein</p></div></div>

      <div style={{ marginBottom: 12 }}>
        <Note kind="bad">
          <b>Zaroori:</b> Bot ko pehle hamesha PAPER mode me 2–4 hafte chalayein. Live mode me ye
          aapke asli paise se orders dalega — backtest ka profit live profit ki guarantee nahi hai.
        </Note>
      </div>

      <div className="split">
        <Panel title="Naya bot">
          <Field label="Instrument">
            <InstrumentSearch placeholder="Stock dhoondein…" onPick={setInstrument} />
          </Field>
          <PickedNote instrument={instrument} />

          <StrategyPicker
            strategies={strategies} value={strategy} onChange={setStrategy}
            params={params} onParams={setParams} showDescription={false}
          />

          <div className="cols-2">
            <Select label="Candle interval" value={cfg.interval} onChange={set('interval')}
                    options={[['5m', '5 min'], ['15m', '15 min'], ['30m', '30 min'], ['1h', '1 hour'], ['1d', '1 day']]} />
            <Num label="Quantity" value={cfg.quantity} onChange={set('quantity')} min="1" />
            <Select label="Product" value={cfg.product} onChange={set('product')}
                    options={[['I', 'Intraday'], ['D', 'Delivery']]} />
            <Num label="Max trades/day" value={cfg.max_trades_per_day} onChange={set('max_trades_per_day')} min="1" />
            <Num label="Stop loss %" value={cfg.stop_loss_pct} onChange={set('stop_loss_pct')} step="0.1" />
            <Num label="Target %" value={cfg.target_pct} onChange={set('target_pct')} step="0.1" />
          </div>

          <Check checked={squareOff} onChange={setSquareOff}>3:15 PM par square off</Check>
          <Check checked={marketHours} onChange={setMarketHours} style={{ marginBottom: 12 }}>
            Sirf market hours me
          </Check>

          <button className="btn primary lg block" onClick={start}>
            <Icon name="play" />Bot start karein
          </button>
        </Panel>

        <div className="stack">
          <Panel title="Chal rahe bots">
            {!active.length
              ? <Empty icon="bot">Koi bot nahi chal raha</Empty>
              : active.map((b) => (
                <div className="bot" key={b.run_id}>
                  <div className="top">
                    <span className="sym">{b.symbol}</span>
                    <Pill kind={b.mode === 'paper' ? 'ok' : 'bad live'}>{b.mode}</Pill>
                    <Pill kind={b.status === 'running' ? 'info' : ''}>{b.status}</Pill>
                    <div className="spacer" />
                    <button className="btn ghost sm" onClick={() => setSelected(b.run_id)}>Log</button>
                    <button className="btn danger sm" onClick={() => stop(b.run_id)}>
                      <Icon name="stop" />Stop
                    </button>
                  </div>
                  <div className="meta" style={{ marginTop: 4 }}>
                    {b.strategy} · {b.interval} · qty {b.quantity}
                  </div>
                  <div className="grid">
                    <Cell k="Position" v={b.position_qty ? `${b.position_qty} @ ${fmt(b.entry_price)}` : 'flat'} />
                    <Cell k="Trades aaj" v={`${b.trades_today}/${b.max_trades_per_day}`} />
                    <Cell k="Last signal" v={b.last_signal || '—'}
                          cls={b.last_signal === 'BUY' ? 'up' : b.last_signal === 'SELL' ? 'down' : ''} />
                    <Cell k="Last check" v={String(b.last_check || '—').slice(11, 19) || '—'} />
                  </div>
                  {b.last_error && (
                    <div style={{ marginTop: 9 }}><Note kind="bad">{b.last_error}</Note></div>
                  )}
                </div>
              ))}
          </Panel>

          <Panel
            title="Bot log"
            right={<span className="muted mono" style={{ fontSize: 11 }}>
              {selected ? 'run #' + selected : ''}
            </span>}
          >
            <div className="log">
              {typeof log === 'string' ? log : log.map((r, i) => (
                <div key={i}>
                  <span className="t">{r.ts.slice(11, 19)}</span>{' '}
                  <span className={r.level}>{r.level.toUpperCase().padEnd(5)}</span>{' '}
                  {r.message}
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </>
  )
}

function Cell({ k, v, cls = '' }) {
  return (
    <div className="cell">
      <div className="k">{k}</div>
      <div className={'v ' + cls}>{v}</div>
    </div>
  )
}
