import { useCallback, useEffect, useState } from 'react'
import { cls, fmt, fmtInt, get, rs, sign } from '../api.js'
import Icon from '../components/Icon.jsx'
import { AllocationBar, Panel, SideTag, SkeletonRows, SkeletonStats, Stat, Table } from '../components/ui.jsx'

const ORDER_COLS = [
  { label: 'Time', get: (r) => <span className="muted mono">{String(r.order_timestamp || '').slice(0, 19).replace('T', ' ')}</span> },
  { label: 'Symbol', get: (r) => <span className="sym-cell">{r.tradingsymbol || r.trading_symbol || ''}</span> },
  { label: 'Side', get: (r) => <SideTag side={r.transaction_type} /> },
  { label: 'Qty', num: true, get: (r) => fmtInt(r.quantity) },
  { label: 'Price', num: true, get: (r) => fmt(r.average_price || r.price) },
  { label: 'Type', get: (r) => <span className="muted">{r.order_type}</span> },
  { label: 'Product', get: (r) => <span className="muted">{r.product}</span> },
  { label: 'Status', get: (r) => <span className={r.status === 'complete' ? 'up' : 'dim'}>{r.status}</span> },
  { label: 'Source', get: (r) => <span className="muted">{r.tag || ''}</span> },
]

const POSITION_COLS = [
  { label: 'Symbol', get: (r) => <span className="sym-cell">{r.tradingsymbol || r.instrument_token}</span> },
  { label: 'Qty', num: true, get: (r) => fmtInt(r.quantity) },
  { label: 'Avg', num: true, get: (r) => fmt(r.average_price) },
  { label: 'LTP', num: true, get: (r) => fmt(r.last_price) },
  { label: 'P&L', num: true, get: (r) => fmt(r.pnl), cls: (r) => cls(r.pnl) },
]

const HOLDING_COLS = [
  { label: 'Symbol', get: (r) => <span className="sym-cell">{r.tradingsymbol || r.trading_symbol || ''}</span> },
  ...POSITION_COLS.slice(1),
]

export function OrdersView() {
  const [rows, setRows] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try { setRows(await get('/orders')); setError(null) }
    catch (e) { setRows([]); setError(e.message) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <>
      <div className="page-head">
        <div><h1>Orders</h1><p>Aaj ki saari order activity</p></div>
        <div className="spacer" />
        <button className="btn ghost sm" onClick={load}><Icon name="refresh" />Reload</button>
      </div>
      <Panel bodyClass="flush" bodyStyle={{ maxHeight: 'calc(100vh - 150px)' }}>
        {loading
          ? <SkeletonRows n={6} />
          : <Table cols={ORDER_COLS} rows={rows} empty={error || 'Abhi koi order nahi'} />}
      </Panel>
    </>
  )
}

export function PortfolioView() {
  const [summary, setSummary] = useState(null)
  const [positions, setPositions] = useState([])
  const [holdings, setHoldings] = useState([])
  const [funds, setFunds] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    (async () => {
      try { setSummary(await get('/portfolio/summary')); setError(null) }
      catch (e) { setError(e.message) }
      try { setPositions(await get('/portfolio/positions')) } catch { setPositions([]) }
      try { setHoldings(await get('/portfolio/holdings')) } catch { setHoldings([]) }
      try { setFunds(JSON.stringify(await get('/portfolio/funds'), null, 2)) }
      catch (e) { setFunds(e.message) }
      setLoading(false)
    })()
  }, [])

  // Share of capital per symbol, from holdings if present else open positions.
  const source = holdings.length ? holdings : positions
  const allocation = source
    .map((r) => ({
      label: r.tradingsymbol || r.trading_symbol || r.instrument_token || '?',
      value: Math.abs(Number(r.last_price || r.average_price || 0) * Number(r.quantity || 0)),
    }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 8)

  return (
    <>
      <div className="page-head"><div><h1>Portfolio</h1><p>Positions, holdings aur P&amp;L</p></div></div>

      <div className="stats">
        {loading
          ? <SkeletonStats n={6} />
          : !summary
          ? <div className="empty">{error || 'Summary nahi mili'}</div>
          : (
            <>
              <Stat label="Day P&L" value={rs(summary.day_pnl)}
                    accent={summary.day_pnl >= 0 ? 'g' : 'r'} colour={cls(summary.day_pnl)} />
              <Stat label="Open positions" value={fmtInt(summary.open_positions)} accent="a" />
              <Stat label="Holdings" value={fmtInt(summary.holdings_count)} accent="a" />
              <Stat label="Invested" value={rs(summary.invested)} />
              <Stat label="Current value" value={rs(summary.current_value)} />
              <Stat label="Holdings P&L" value={rs(summary.holdings_pnl)}
                    accent={summary.holdings_pnl >= 0 ? 'g' : 'r'} colour={cls(summary.holdings_pnl)}
                    sub={`${sign(summary.holdings_pnl_pct)}${fmt(summary.holdings_pnl_pct)}%`} />
            </>
          )}
      </div>

      {allocation.length > 1 && (
        <div style={{ marginTop: 12 }}>
          <Panel title="Allocation">
            <AllocationBar items={allocation} />
          </Panel>
        </div>
      )}

      <div className="cols-2" style={{ marginTop: 12 }}>
        <Panel title="Open positions" bodyClass="flush" bodyStyle={{ maxHeight: 340 }}>
          <Table cols={POSITION_COLS} rows={positions} empty="Koi open position nahi" />
        </Panel>
        <Panel title="Holdings" bodyClass="flush" bodyStyle={{ maxHeight: 340 }}>
          <Table cols={HOLDING_COLS} rows={holdings} empty="Paper mode me holdings khali rehti hain" />
        </Panel>
      </div>

      <div style={{ marginTop: 12 }}>
        <Panel title="Funds & margin">
          <div className="log">{funds}</div>
        </Panel>
      </div>
    </>
  )
}
