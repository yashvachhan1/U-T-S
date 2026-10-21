/* Risk: what the limits are, how close we are, and the switch that stops it.
 *
 * The state banner leads and is impossible to miss, because the question
 * this view exists to answer is "can the system trade right now, and if
 * not, why not". Everything else is how much room is left before it cannot.
 *
 * Each limit is a meter rather than a number in a table: "3 of 5 positions"
 * is read correctly, but "how close am I" is read instantly.
 */
import { useCallback, useEffect, useState } from 'react'
import { fmt, fmtInt, get, post, rs } from '../api.js'
import Icon from '../components/Icon.jsx'
import {
  Empty, KeyValues, Meter, Panel, SkeletonRows, Stat, StatusTag,
} from '../components/ui.jsx'
import { useInterval } from '../hooks/index.js'
import { useUI } from '../components/Feedback.jsx'

const STATE_COPY = {
  running: {
    tone: 'running', icon: 'check', title: 'Trading chalu hai',
    note: 'Saari limits ke andar. Naye orders ja sakte hain.',
  },
  paused: {
    tone: 'paused', icon: 'pause', title: 'Paused — naya entry band',
    note: 'Wajah khatam hote hi apne aap chalu ho jayega. Exit abhi bhi allowed hai.',
  },
  halted: {
    tone: '', icon: 'alert', title: 'HALTED — naya order nahi jayega',
    note: 'Ye apne aap clear nahi hoga. Niche naam daal kar reset karein. Position band karna aapka faisla hai — halt khud kabhi position band nahi karta.',
  },
}

export default function Risk() {
  const { toast, confirm } = useUI()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [who, setWho] = useState('')
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    try { setData(await get('/risk/status')); setError(null) }
    catch (e) { setError(e.message) }
  }, [])

  useEffect(() => { load() }, [load])
  useInterval(load, 15000)

  const stopAll = async () => {
    const ok = await confirm({
      title: 'STOP ALL?',
      note: 'Saare bots band, naye order band. Khuli positions chhedi nahi jayengi — unhe band karna aapka faisla hai.',
      danger: true,
    })
    if (!ok) return
    setBusy('stop')
    try {
      await post('/risk/stop', { by: who.trim() || 'dashboard', detail: 'dashboard se STOP ALL' })
      toast('Trading halt kar di gayi', 'ok')
      await load()
    } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }

  const reset = async () => {
    if (!who.trim()) {
      toast('Reset ke liye naam zaroori hai — record me jaata hai', 'err')
      return
    }
    setBusy('reset')
    try {
      await post('/risk/reset', { by: who.trim() })
      toast('Halt clear ho gaya', 'ok')
      await load()
    } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }

  const sweep = async () => {
    setBusy('sweep')
    try {
      const out = await post('/risk/sweep')
      toast(out.tripped?.length
        ? `Trip hua: ${out.tripped.join(', ')}`
        : 'Saari limits ke andar', out.tripped?.length ? 'err' : 'ok')
      await load()
    } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }

  if (!data) {
    return (
      <>
        <Head onReload={load} />
        <Panel>{error ? <Empty icon="alert">{error}</Empty> : <SkeletonRows n={6} />}</Panel>
      </>
    )
  }

  const sw = data.switches || {}
  const acc = data.account || {}
  const lim = data.limits || {}
  const exp = acc.exposure || {}
  const copy = STATE_COPY[sw.state] || STATE_COPY.halted
  const capital = lim.trading_capital || 0

  return (
    <>
      <Head onReload={load} />

      <div className={'halt-banner ' + copy.tone}>
        <Icon name={copy.icon} />
        <div style={{ flex: 1, minWidth: 220 }}>
          <div className="halt-title">{copy.title}</div>
          <div className="halt-why">{copy.note}</div>
          {(sw.reasons || []).map((r, i) => (
            <div className="halt-why" key={i}>
              <code>{r.trigger}</code> — {r.detail} <span className="muted">({r.at})</span>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input type="text" placeholder="aapka naam"
                 value={who} onChange={(e) => setWho(e.target.value)}
                 style={{ width: 132, padding: '6px 9px', fontSize: 12 }} />
          {sw.state === 'running' ? (
            <button className="btn danger sm" onClick={stopAll} disabled={busy === 'stop'}>
              <Icon name="stop" />STOP ALL
            </button>
          ) : (
            <button className="btn primary sm" onClick={reset} disabled={busy === 'reset'}>
              <Icon name="power" />Reset
            </button>
          )}
        </div>
      </div>

      {capital <= 0 && (
        <div className="halt-banner paused">
          <Icon name="alert" />
          <div>
            <div className="halt-title">TRADING_CAPITAL set nahi hai</div>
            <div className="halt-why">
              Risk engine har position isi ke against size karta hai. Set kiye bina
              wo size karne se mana kar dega — guess nahi karega. <code>.env</code> me daalein.
            </div>
          </div>
        </div>
      )}

      <div className="stats" style={{ marginBottom: 12 }}>
        <Stat label="Aaj ka P&L" value={rs(acc.realised_pnl_today)}
              colour={acc.realised_pnl_today > 0 ? 'up' : acc.realised_pnl_today < 0 ? 'down' : ''}
              accent={acc.realised_pnl_today < 0 ? 'r' : acc.realised_pnl_today > 0 ? 'g' : ''} />
        <Stat label="Capital" value={rs(capital)} />
        <Stat label="Peak equity" value={rs(acc.peak_equity)} sub="Drawdown isi se naapa jaata hai" />
        <Stat label="Trades aaj" value={fmtInt(acc.trades_today)}
              sub={`${acc.wins ?? 0}W / ${acc.losses ?? 0}L`} />
        <Stat label="Lagataar loss" value={fmtInt(acc.consecutive_losses)}
              accent={acc.consecutive_losses >= 5 ? 'r' : ''}
              sub={acc.consecutive_losses >= 5 ? 'Size aadhi ho rahi hai' : ''} />
        <Stat label="Open positions" value={fmtInt(exp.open_positions ?? 0)} />
      </div>

      <div className="cols-2">
        <Panel title="Kill switch limits">
          <Meter label="Aaj ka loss" value={acc.daily_loss_pct ?? 0}
                 limit={lim.daily_loss_halt_pct ?? 3} unit="%"
                 hint="Yahan pahunche to sab band, din khatam." />
          <Meter label="Drawdown (peak se)" value={acc.drawdown_pct ?? 0}
                 limit={lim.max_drawdown_halt_pct ?? 10} unit="%"
                 hint="Yahan pahunche to manual restart chahiye." />
          <Meter label="Hafte ka loss" value={acc.weekly_loss_pct ?? 0}
                 limit={6} unit="%" hint="Yahan size aadhi ho jaati hai, halt nahi." />
          <Meter label="Order reject rate" value={acc.reject_rate_pct ?? 0}
                 limit={20} unit="%" hint="Yahan trading pause ho jaati hai." />
        </Panel>

        <Panel title="Position limits">
          <Meter label="Open positions" value={exp.open_positions ?? 0}
                 limit={lim.max_open_positions ?? 5} />
          <Meter label="Trades aaj" value={acc.trades_today ?? 0}
                 limit={lim.max_trades_per_day ?? 10}
                 hint="Overtrading rokne ke liye." />
          <Meter label="Total exposure" value={exp.total_pct ?? 0}
                 limit={lim.max_total_exposure_pct ?? 80} unit="%"
                 hint="Cash buffer bachana zaroori hai." />
          <div style={{ marginTop: 16 }}>
            <KeyValues rows={[
              ['Max risk per trade', `${fmt(lim.max_risk_per_trade_pct, 1)}% capital`],
              ['Max position size', `${fmt(lim.max_position_pct, 0)}% capital`],
              ['Max per sector', `${fmt(lim.max_sector_exposure_pct, 0)}% capital`],
              ['Ek industry me max', `${lim.max_correlated_positions ?? 2} positions`],
            ]} />
          </div>
        </Panel>
      </div>

      <div className="cols-2" style={{ marginTop: 12 }}>
        <Panel title="Sector exposure">
          {Object.keys(exp.by_sector_pct || {}).length === 0 ? (
            <Empty icon="wallet">Koi position nahi.</Empty>
          ) : (
            Object.entries(exp.by_sector_pct).map(([sector, pct]) => (
              <Meter key={sector} label={sector} value={pct}
                     limit={lim.max_sector_exposure_pct ?? 30} unit="%" />
            ))
          )}
        </Panel>

        <Panel title="Switch kya karta hai"
               right={<button className="btn ghost sm" onClick={sweep} disabled={busy === 'sweep'}>
                 {busy === 'sweep' ? <i className="spin" /> : <Icon name="shield" />}Abhi check karo
               </button>}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <SwitchRow tone="good" icon="check" title="Exit hamesha allowed"
                       note="Aisi koi state nahi hai jisme position band na ho sake. Nikal na paana har us wajah se bura hai jo halt karati hai." />
            <SwitchRow tone="warn" icon="pause" title="Paused khud clear hota hai"
                       note="Stale feed aur order rejects — wajah khatam, pause khatam." />
            <SwitchRow tone="crit" icon="alert" title="Halted sirf insaan clear karta hai"
                       note="Naam ke saath, aur restart se bhi nahi jaata — disk par likha hai." />
            <SwitchRow tone="muted" icon="info" title="Halt position band nahi karta"
                       note="Automatic panic-exit din ke sabse bure price par hota hai. Band karna aapka faisla." />
          </div>
          {sw.last_cleared_by && (
            <div style={{ marginTop: 14 }}>
              <KeyValues rows={[['Pichhli baar clear kiya', sw.last_cleared_by]]} />
            </div>
          )}
        </Panel>
      </div>
    </>
  )
}

function Head({ onReload }) {
  return (
    <div className="page-head">
      <div>
        <h1>Risk</h1>
        <p>Limits, kill switches, aur kitni jagah bachi hai</p>
      </div>
      <div className="spacer" />
      <button className="btn ghost sm" onClick={onReload}><Icon name="refresh" />Reload</button>
    </div>
  )
}

function SwitchRow({ tone, icon, title, note }) {
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
      <StatusTag tone={tone} icon={icon}>{title}</StatusTag>
      <span style={{ fontSize: 11.5, color: 'var(--text-2)', lineHeight: 1.5, flex: 1 }}>
        {note}
      </span>
    </div>
  )
}
