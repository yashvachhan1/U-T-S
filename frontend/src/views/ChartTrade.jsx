import { useCallback, useEffect, useState } from 'react'
import { INTERVALS, cls, del, fmt, fmtInt, get, lookbackFor, post, rs, sign } from '../api.js'
import Icon from '../components/Icon.jsx'
import InstrumentSearch from '../components/InstrumentSearch.jsx'
import { PriceChart } from '../components/charts.jsx'
import { useUI } from '../components/Feedback.jsx'
import { useFlash, useInterval, usePriceHistory } from '../hooks/index.js'
import Sparkline from '../components/Sparkline.jsx'
import { Check, Empty, Field, Note, Panel, Seg, SideTag } from '../components/ui.jsx'

export default function ChartTrade({ status, theme, onLogin, pending, onPendingHandled }) {
  const { toast, confirm } = useUI()

  const [watchlist, setWatchlist] = useState([])
  const [selected, setSelected] = useState(null)
  const [interval, setInterval_] = useState('1d')
  const [candles, setCandles] = useState([])
  const [quote, setQuote] = useState(null)
  const [hover, setHover] = useState(null)
  const [chartMsg, setChartMsg] = useState(null)
  const [auto, setAuto] = useState(true)
  const [orders, setOrders] = useState([])
  const history = usePriceHistory(watchlist)

  const [side, setSide] = useState('BUY')
  const [qty, setQty] = useState(1)
  const [orderType, setOrderType] = useState('MARKET')
  const [product, setProduct] = useState('D')
  const [price, setPrice] = useState(0)
  const [trigger, setTrigger] = useState(0)

  /* ------------------------------------------------------------ data */
  const loadWatchlist = useCallback(async () => {
    try { setWatchlist(await get('/watchlist')) } catch { /* not connected yet */ }
  }, [])

  const loadOrders = useCallback(async () => {
    try { setOrders((await get('/orders')).slice(0, 6)) } catch { setOrders([]) }
  }, [])

  const loadChart = useCallback(async (inst, iv) => {
    if (!inst) return
    try {
      const data = await get(
        `/market/candles?instrument_key=${encodeURIComponent(inst.instrument_key)}` +
        `&interval=${iv}&days=${lookbackFor(iv)}`)
      const rows = data.candles || []
      setCandles(rows)
      setChartMsg(rows.length ? null : {
        title: 'Is interval par data nahi mila',
        detail: 'Market band hone par intraday candles nahi aate — 1D try karein.',
      })
    } catch (e) {
      // Shown in place, not as a toast: this also runs on a timer.
      setChartMsg({
        title: status.logged_in ? 'Chart load nahi hua' : 'Upstox se connect nahi hai',
        detail: e.message,
        login: !status.logged_in,
      })
    }
  }, [status.logged_in])

  const loadQuote = useCallback(async (inst) => {
    if (!inst) return
    try { setQuote(await get(`/market/quote?instrument_key=${encodeURIComponent(inst.instrument_key)}`)) }
    catch { /* market closed or signed out */ }
  }, [])

  useEffect(() => { loadWatchlist(); loadOrders() }, [loadWatchlist, loadOrders])
  useEffect(() => { loadChart(selected, interval); loadQuote(selected) }, [selected, interval, loadChart, loadQuote])

  useInterval(() => {
    if (!auto || !status.logged_in) return
    loadQuote(selected)
    loadWatchlist()
  }, 5000)

  /* --------------------------------------------------------- actions */
  const addToWatchlist = async (inst) => {
    try {
      await post('/watchlist', { instrument_key: inst.instrument_key })
      setSelected(inst)
      loadWatchlist()
    } catch (e) { toast(e.message, 'err') }
  }

  // An instrument chosen in the command palette arrives through `pending`.
  useEffect(() => {
    if (!pending) return
    addToWatchlist(pending)
    onPendingHandled()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending])

  const removeFromWatchlist = async (key, e) => {
    e.stopPropagation()
    await del(`/watchlist?instrument_key=${encodeURIComponent(key)}`)
    if (selected && selected.instrument_key === key) setSelected(null)
    loadWatchlist()
  }

  const marketish = orderType === 'MARKET' || orderType === 'SL-M'
  const refPrice = marketish ? Number(quote?.ltp || 0) : Number(price || 0)
  const orderValue = refPrice && qty ? rs(refPrice * qty) : '—'

  const placeOrder = async () => {
    if (!selected) return toast('Pehle koi stock chunein', 'err')
    const live = !status.paper_trading
    const ok = await confirm({
      title: live ? 'LIVE ORDER confirm karein' : 'Paper order confirm karein',
      danger: live,
      okText: side,
      rows: [
        ['Symbol', selected.symbol],
        ['Side', side, side === 'BUY' ? 'up' : 'down'],
        ['Quantity', String(qty)],
        ['Order type', orderType],
        ['Product', product === 'D' ? 'Delivery' : 'Intraday'],
        ['Order value', orderValue],
      ],
      note: live
        ? 'Ye order asli paise se Upstox par lagega.'
        : 'Paper mode — Upstox tak nahi jayega, sirf local record hoga.',
    })
    if (!ok) return
    try {
      const r = await post('/orders', {
        instrument_key: selected.instrument_key,
        side,
        quantity: Number(qty),
        order_type: orderType,
        product,
        price: Number(price) || 0,
        trigger_price: Number(trigger) || 0,
      })
      toast(r.message || 'Order chala gaya', 'ok')
      loadOrders()
    } catch (e) { toast(e.message, 'err') }
  }

  /* ------------------------------------------------------------- ui */
  const bar = hover || candles[candles.length - 1]
  const change = quote?.change
  const flash = useFlash(quote?.ltp)

  return (
    <div className="terminal">

      {/* ---------------------------------------------- watchlist */}
      <Panel
        title="Watchlist"
        right={<span className="muted mono" style={{ fontSize: 11 }}>{watchlist.length || ''}</span>}
        bodyClass="tight"
      >
        <div style={{ padding: '0 2px 10px' }}>
          <InstrumentSearch onPick={addToWatchlist} />
        </div>
        {!watchlist.length
          ? <Empty icon="search">Search karke stock add karein</Empty>
          : (
            <div className="wl">
              {watchlist.map((r) => (
                <div
                  key={r.instrument_key}
                  className={'row ' + (selected?.instrument_key === r.instrument_key ? 'on' : '')}
                  onClick={() => setSelected({ instrument_key: r.instrument_key, symbol: r.symbol, name: r.name })}
                >
                  <div style={{ minWidth: 0 }}>
                    <div className="s">{r.symbol}</div>
                    <div className="x-ex">{r.exchange || ''}</div>
                  </div>
                  <Sparkline points={history[r.instrument_key]} />
                  <div>
                    <div className="p">{fmt(r.ltp)}</div>
                    <div className={'c ' + cls(r.change_pct)}>
                      {r.change_pct != null ? sign(r.change_pct) + fmt(r.change_pct) + '%' : ''}
                    </div>
                  </div>
                  <span className="del" onClick={(e) => removeFromWatchlist(r.instrument_key, e)}>×</span>
                </div>
              ))}
            </div>
          )}
      </Panel>

      {/* -------------------------------------------------- chart */}
      <div className="panel">
        <div className="chart-head">
          <div>
            <div className="sym">{selected ? selected.symbol : 'Koi stock chunein'}</div>
            <div className="nm">{selected ? selected.name : 'Left panel se search karein'}</div>
          </div>
          <div className="spacer" />
          <div style={{ textAlign: 'right' }}>
            <div className={`px ${cls(change)} ${flash}`}>{fmt(quote?.ltp)}</div>
            <div className={'chg ' + cls(change)}>
              {change != null
                ? `${sign(change)}${fmt(change)}  (${sign(quote.change_pct)}${fmt(quote.change_pct)}%)`
                : '—'}
            </div>
          </div>
        </div>

        <div className="chart-tools">
          <Seg value={interval} onChange={setInterval_} options={INTERVALS.map(([k, l]) => [k, l])} />
          <div className="ohlc">
            {bar && ['open', 'high', 'low', 'close'].map((k) => (
              <span key={k}>
                {k[0].toUpperCase()} <b className={bar.close >= bar.open ? 'up' : 'down'}>{fmt(bar[k])}</b>
              </span>
            ))}
          </div>
          <div className="spacer" />
          <Check checked={auto} onChange={setAuto}>Auto</Check>
        </div>

        <div className="chart-wrap">
          <PriceChart candles={candles} theme={theme} onHover={setHover} />
          {chartMsg && (
            <div className="chart-msg on">
              <div>
                <Icon name="chart" />
                <div className="t">{chartMsg.title}</div>
                <div className="d">{chartMsg.detail}</div>
                {chartMsg.login && <button className="btn primary" onClick={onLogin}>Upstox Login</button>}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ------------------------------------------- order ticket */}
      <Panel title="Order Ticket" className="ticket">
        <div style={{ marginBottom: 12 }}>
          {!status.credentials_configured
            ? <Note kind="warn">Upstox API key set nahi hai. <b>Setup</b> tab dekhein.</Note>
            : !status.logged_in
              ? <Note kind="warn">Login nahi hai. Upar <b>Upstox Login</b> dabayein.</Note>
              : !status.paper_trading
                ? <Note kind="bad"><b>LIVE MODE</b> — order asli paise se lagega.</Note>
                : <Note kind="good">Paper mode — order Upstox tak nahi jayega.</Note>}
        </div>

        <Seg className="wide side" value={side} onChange={setSide} options={[['BUY', 'BUY'], ['SELL', 'SELL']]} />
        <div style={{ height: 12 }} />

        <Field label="Quantity">
          <input type="number" min="1" value={qty} onChange={(e) => setQty(e.target.value)} />
        </Field>

        <div className="cols-2">
          <Field label="Order type">
            <select value={orderType} onChange={(e) => setOrderType(e.target.value)}>
              {['MARKET', 'LIMIT', 'SL', 'SL-M'].map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Product">
            <select value={product} onChange={(e) => setProduct(e.target.value)}>
              <option value="D">Delivery</option>
              <option value="I">Intraday</option>
            </select>
          </Field>
        </div>

        <div className="cols-2">
          <div style={{ opacity: marketish ? 0.4 : 1 }}>
            <Field label="Price">
              <input type="number" step="0.05" value={price} disabled={marketish}
                     onChange={(e) => setPrice(e.target.value)} />
            </Field>
          </div>
          <div style={{ opacity: orderType === 'MARKET' || orderType === 'LIMIT' ? 0.4 : 1 }}>
            <Field label="Trigger">
              <input type="number" step="0.05" value={trigger}
                     disabled={orderType === 'MARKET' || orderType === 'LIMIT'}
                     onChange={(e) => setTrigger(e.target.value)} />
            </Field>
          </div>
        </div>

        <div className="sum">
          <span className="k">Order value</span>
          <span className="v">{orderValue}</span>
        </div>

        <button className={'btn lg block ' + (side === 'BUY' ? 'buy' : 'sell')} onClick={placeOrder}>
          {side}
        </button>

        <div style={{ height: 14 }} />
        <div className="sub-h">Aaj ke orders</div>
        {!orders.length
          ? <div className="empty" style={{ padding: 18 }}>Aaj koi order nahi</div>
          : (
            <table className="tbl">
              <tbody>
                {orders.map((o, i) => (
                  <tr key={i}>
                    <td><SideTag side={o.transaction_type} /></td>
                    <td className="sym-cell">{o.tradingsymbol || ''}</td>
                    <td className="num">{fmtInt(o.quantity)}</td>
                    <td className="num">{fmt(o.average_price || o.price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </Panel>
    </div>
  )
}
