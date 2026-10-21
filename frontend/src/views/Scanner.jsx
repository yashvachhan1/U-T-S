/* The scanner: where signals come from, and whether they are acted on.
 *
 * The two switches are drawn differently on purpose. Searching for signals
 * and acting on them are separate decisions in the backend, and a UI that
 * showed them as two identical checkboxes would quietly undo that -- the
 * gap between them is where a person gets to look at what the strategy
 * produces before it spends money.
 *
 * So "Signal dhoondo" is an ordinary toggle, and "Order bhejo" asks first.
 *
 * The rejections list is the other half of the page. Most signals are
 * refused, and that is the system working; "why did it not trade" is the
 * question asked on every quiet day, and the answer has to be here.
 */
import { useCallback, useEffect, useState } from 'react'
import { fmt, fmtInt, get, post } from '../api.js'
import Icon from '../components/Icon.jsx'
import {
  Empty, KeyValues, Note, Panel, Select, SkeletonRows, Stat, StatusTag,
} from '../components/ui.jsx'
import { useInterval } from '../hooks/index.js'
import { useUI } from '../components/Feedback.jsx'

const INTERVALS = [['1m', '1m'], ['3m', '3m'], ['5m', '5m'], ['15m', '15m'],
                   ['30m', '30m'], ['1h', '1H'], ['1d', '1D']]
const PRODUCTS = [['I', 'Intraday'], ['D', 'Delivery']]

/* The backend sends symbols alongside the keys, because an equity key is
   an ISIN and "INE002A01018" tells a reader nothing. */
const symbolsOf = (data) =>
  data.watchlist_symbols && data.watchlist_symbols.length
    ? data.watchlist_symbols
    : (data.watchlist || []).map((k) => String(k).split('|').pop())

export default function Scanner({ strategies = [] }) {
  const { toast, confirm } = useUI()
  const [data, setData] = useState(null)
  const [last, setLast] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    try { setData(await get('/scanner/status')); setError(null) }
    catch (e) { setError(e.message) }
  }, [])

  useEffect(() => { load() }, [load])
  useInterval(load, 15000)

  const configure = async (patch, message) => {
    setBusy('config')
    try {
      await post('/scanner/config', patch)
      if (message) toast(message, 'ok')
      await load()
    } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }

  const toggleEnabled = () => {
    const on = !data.config.enabled
    configure({ enabled: on },
      on ? 'Scanner chalu — signal dhoondega, bhejega nahi'
         : 'Scanner band')
  }

  const toggleExecute = async () => {
    const on = !data.config.auto_execute
    if (on) {
      const ok = await confirm({
        title: 'Order bhejna chalu karein?',
        note: data.paper_trading
          ? 'Paper mode hai — order local record me jayega, Upstox tak nahi. '
            + 'Phir bhi: ab se har approved signal order ban jayega.'
          : 'LIVE MODE — ab se har approved signal ASLI order ban jayega.',
        danger: !data.paper_trading,
      })
      if (!ok) return
    }
    configure({ auto_execute: on },
      on ? 'Ab approved signals order ban kar jayenge' : 'Order bhejna band')
  }

  const runOnce = async (execute) => {
    setBusy(execute ? 'execute' : 'dry')
    try {
      const out = await post(`/scanner/run?execute=${execute}`)
      setLast(out)
      if (out.skipped) toast(`Skip: ${out.skipped}`, 'err')
      else toast(`${out.signals} signal, ${out.approved} approved`
                 + (execute ? `, ${out.executed} bheje` : ''), 'ok')
      await load()
    } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }

  if (!data) {
    return (
      <>
        <Head onReload={load} />
        <Panel>{error ? <Empty icon="alert">{error}</Empty> : <SkeletonRows n={5} />}</Panel>
      </>
    )
  }

  const cfg = data.config
  const stats = data.stats
  const names = symbolsOf(data)
  const strategyOptions = strategies.length
    ? strategies.map((s) => [s.key, s.name])
    : [[cfg.strategy, cfg.strategy]]

  return (
    <>
      <Head onReload={load} />

      {!names.length && (
        <div className="halt-banner">
          <Icon name="alert" />
          <div>
            <div className="halt-title">Watchlist khali hai</div>
            <div className="halt-why">
              Scanner ke paas dekhne ko kuch nahi. Chart &amp; Trade page se
              instruments add karein — warna paper run do mahine chalega aur
              zero trade karega.
            </div>
          </div>
        </div>
      )}

      <div className="cols-2" style={{ marginBottom: 12 }}>
        <Panel title="Do switch, do alag faisle">
          <Switch
            on={cfg.enabled}
            busy={busy === 'config'}
            onToggle={toggleEnabled}
            title="Signal dhoondo"
            note="Watchlist par strategy chalti hai aur signal risk engine tak
                  jaata hai. Order kuch nahi jaata."
          />
          <Switch
            on={cfg.auto_execute}
            busy={busy === 'config'}
            onToggle={toggleExecute}
            danger
            title="Order bhejo"
            note="Approved signal ab order ban jayega. Pehle ek-do din sirf
                  upar wala chalu rakh kar dekh lein ki kya mil raha hai."
          />

          <Note kind="warn">
            Ye dono <b>restart par band</b> ho jaate hain — jaan-boojhkar. Jo
            scanner restart jhel jaye, wo ek din trade shuru kar dega kyunki
            machine reboot hui thi.
          </Note>

          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <button className="btn ghost sm" onClick={() => runOnce(false)}
                    disabled={busy === 'dry'}>
              {busy === 'dry' ? <i className="spin" /> : <Icon name="search" />}
              Ek baar dekho
            </button>
            <button className="btn sm" onClick={() => runOnce(true)}
                    disabled={busy === 'execute'}>
              {busy === 'execute' ? <i className="spin" /> : <Icon name="zap" />}
              Dekho aur bhejo
            </button>
          </div>
        </Panel>

        <Panel title="Kya scan hoga">
          <Select label="Strategy" value={cfg.strategy}
                  options={strategyOptions}
                  onChange={(v) => configure({ strategy: v }, `Strategy: ${v}`)} />
          <div className="cols-2" style={{ gap: 10, marginTop: 10 }}>
            <Select label="Interval" value={cfg.interval} options={INTERVALS}
                    onChange={(v) => configure({ interval: v })} />
            <Select label="Product" value={cfg.product} options={PRODUCTS}
                    onChange={(v) => configure({ product: v })} />
          </div>

          <div style={{ marginTop: 14 }}>
            <KeyValues rows={[
              ['Expected move', `${fmt(cfg.expected_move_pct, 1)}%`],
              ['Probability', fmt(cfg.probability, 2)],
              ['Instruments', `${names.length}${names.length ? ': ' + names.slice(0, 3).join(', ') : ''}${names.length > 3 ? '…' : ''}`],
              ['Market', data.market_open ? 'khula' : 'band'],
            ]} />
          </div>

          <Note kind="">
            Expected move aur probability edge test me jaate hain — in dono ka
            guna cost se zyada nahi hua to trade reject ho jayega. Rule-based
            strategy koi probability model nahi hai, isliye default
            jaan-boojhkar mamooli rakha gaya hai.
          </Note>
        </Panel>
      </div>

      <div className="stats" style={{ marginBottom: 12 }}>
        <Stat label="Scans" value={fmtInt(stats.scans)} sub={stats.last_run || '—'} />
        <Stat label="Signals mile" value={fmtInt(stats.signals)} />
        <Stat label="Proposed" value={fmtInt(stats.proposed)} />
        <Stat label="Approved" value={fmtInt(stats.approved)}
              accent={stats.approved > 0 ? 'g' : ''}
              colour={stats.approved > 0 ? 'up' : ''} />
        <Stat label="Order bheje" value={fmtInt(stats.executed)}
              accent={stats.executed > 0 ? 'a' : ''} />
        <Stat label="Errors" value={stats.last_error ? '1+' : '0'}
              accent={stats.last_error ? 'r' : ''}
              sub={stats.last_error ? String(stats.last_error).slice(0, 36) : ''} />
      </div>

      <Panel title="Pichhli scan"
             right={last && !last.skipped && (
               <StatusTag tone={last.approved > 0 ? 'good' : 'muted'}
                          icon={last.approved > 0 ? 'check' : 'info'}>
                 {last.signals} signal · {last.approved} approved
               </StatusTag>
             )}>
        {!last ? (
          <Empty icon="search">
            Abhi tak haath se scan nahi chalayi. "Ek baar dekho" dabayein —
            kuch bhejega nahi.
          </Empty>
        ) : last.skipped ? (
          <Note kind="warn">Scan nahi chali: {last.skipped}</Note>
        ) : (
          <>
            <KeyValues rows={[
              ['Signal mile', fmtInt(last.signals)],
              ['Risk engine tak gaye', fmtInt(last.proposed)],
              ['Approved', fmtInt(last.approved)],
              ['Order bheje', fmtInt(last.executed)],
            ]} />

            {(last.rejected || []).length > 0 && (
              <div style={{ marginTop: 16 }}>
                <div className="pf-title">Kyun reject hue</div>
                {last.rejected.map((r, i) => (
                  <div className="pf-row warn" key={`${r.symbol}-${i}`}>
                    <Icon name="info" />
                    <div className="pf-body">
                      <div className="pf-name">{r.symbol || '—'}</div>
                      {(r.reasons || []).map((reason, j) => (
                        <div className="pf-detail" key={j}>{reason}</div>
                      ))}
                    </div>
                  </div>
                ))}
                <p style={{ fontSize: 11.5, color: 'var(--text-2)', marginTop: 10,
                            marginBottom: 0, lineHeight: 1.6 }}>
                  Zyadatar signal reject hona <b>normal hai</b>. Edge test akela
                  hi aadhe nikaal deta hai — 0.3% ka move roughly round trip ka
                  kharcha hai, aur usse chhota move trade nahi, brokerage dekar
                  raay batana hai.
                </p>
              </div>
            )}
          </>
        )}
      </Panel>

      <Panel title="Watchlist" style={{ marginTop: 12 }}>
        {!names.length ? (
          <Empty icon="inbox">Khali. Chart &amp; Trade se add karein.</Empty>
        ) : (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {names.map((symbol) => (
              <StatusTag key={symbol} tone="muted">{symbol}</StatusTag>
            ))}
          </div>
        )}
      </Panel>
    </>
  )
}

function Switch({ on, busy, onToggle, title, note, danger }) {
  return (
    <div className="scan-switch">
      <button
        className={'scan-toggle ' + (on ? (danger ? 'on danger' : 'on') : '')}
        onClick={onToggle}
        disabled={busy}
        aria-pressed={on}
      >
        <i />
      </button>
      <div className="scan-body">
        <div className="scan-title">
          {title}
          <StatusTag tone={on ? (danger ? 'warn' : 'good') : 'muted'}
                     icon={on ? 'check' : 'info'}>
            {on ? 'chalu' : 'band'}
          </StatusTag>
        </div>
        <div className="scan-note">{note}</div>
      </div>
    </div>
  )
}

function Head({ onReload }) {
  return (
    <div className="page-head">
      <div>
        <h1>Scanner</h1>
        <p>Signal kahan se aate hain, aur kaunse ruk jaate hain</p>
      </div>
      <div className="spacer" />
      <button className="btn ghost sm" onClick={onReload}>
        <Icon name="refresh" />Reload
      </button>
    </div>
  )
}
