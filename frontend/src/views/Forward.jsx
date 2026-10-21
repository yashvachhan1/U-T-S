/* The paper forward test, read in the order it answers questions.
 *
 * Sessions lead, because the first honest question about a forward test is
 * "has it run long enough to mean anything" -- and for the first six weeks
 * the answer is no, which this page should say plainly rather than filling
 * the space with numbers that are still noise.
 *
 * Then the equity curve, then the comparison against the backtest, then the
 * agents, then the checklist. That is roughly the order in which a finding
 * would change what you do next.
 *
 * One thing the curve cannot show: it plots each session's close, while the
 * drawdown figure is computed from intraday lows. So the drawdown is given
 * its own stat and its own meter against the backtest rather than being
 * left for the eye to estimate off a line that does not contain it.
 */
import { useCallback, useEffect, useState } from 'react'
import { fmt, fmtInt, get, post, rs } from '../api.js'
import { EquityChart } from '../components/charts.jsx'
import Icon from '../components/Icon.jsx'
import {
  Empty, Hero, KeyValues, Meter, Panel, SkeletonRows, Stat, StatusTag, Table,
} from '../components/ui.jsx'
import { useInterval } from '../hooks/index.js'
import { useUI } from '../components/Feedback.jsx'

const MIN_SESSIONS = 40

const SEVERITY = {
  critical: { tone: 'crit', icon: 'alert', label: 'Critical' },
  serious: { tone: 'warn', icon: 'alert', label: 'Serious' },
  warning: { tone: 'warn', icon: 'info', label: 'Warning' },
}

const VERDICT = {
  adds_value: { tone: 'good', icon: 'check', label: 'Kaam ka hai' },
  costs_money: { tone: 'crit', icon: 'alert', label: 'Nuksan kar raha' },
  no_measurable_effect: { tone: 'muted', icon: 'info', label: 'Koi asar nahi' },
  not_enough_evidence: { tone: 'muted', icon: 'info', label: 'Saboot kam' },
  no_aligned_trades: { tone: 'muted', icon: 'info', label: 'Koi aligned trade nahi' },
  silent: { tone: 'muted', icon: 'info', label: 'Chup raha' },
}

const pretty = (name) =>
  String(name).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

const AGENT_COLS = [
  { label: 'Agent', get: (r) => <span className="sym-cell">{pretty(r.agent)}</span> },
  {
    label: 'Verdict',
    get: (r) => {
      const v = VERDICT[r.verdict] || VERDICT.silent
      return <StatusTag tone={v.tone} icon={v.icon}>{v.label}</StatusTag>
    },
  },
  {
    label: 'Edge vs base', num: true,
    get: (r) => (r.edge_vs_base_pct == null
      ? <span className="muted">—</span>
      : <span className={r.edge_vs_base_pct > 0 ? 'up' : 'down'}>
          {r.edge_vs_base_pct > 0 ? '+' : ''}{fmt(r.edge_vs_base_pct, 3)}%
        </span>),
    cls: () => '',
  },
  { label: 'Opinions', num: true, get: (r) => fmtInt(r.opinions) },
  {
    label: 'Aligned / opposed', num: true,
    get: (r) => <span className="muted">{fmtInt(r.aligned)} / {fmtInt(r.opposed)}</span>,
  },
  {
    label: 'Coverage', num: true,
    get: (r) => (r.coverage_pct == null ? '—' : `${fmt(r.coverage_pct, 0)}%`),
  },
]

export default function Forward({ theme }) {
  const { toast } = useUI()
  const [data, setData] = useState(null)
  const [sessions, setSessions] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [report, days] = await Promise.all([
        get('/forward/report'),
        get('/forward/sessions?limit=120'),
      ])
      setData(report)
      setSessions(days.sessions || [])
      setError(null)
    } catch (e) { setError(e.message) }
  }, [])

  useEffect(() => { load() }, [load])
  useInterval(load, 60000)

  const copyReport = async () => {
    setBusy(true)
    try {
      const res = await fetch('/api/forward/report.txt')
      await navigator.clipboard.writeText(await res.text())
      toast('Report copy ho gayi', 'ok')
    } catch (e) { toast(e.message, 'err') }
    setBusy(false)
  }

  if (!data) {
    return (
      <>
        <Head onReload={load} onCopy={copyReport} busy={busy} />
        <Panel>{error ? <Empty icon="alert">{error}</Empty> : <SkeletonRows n={6} />}</Panel>
      </>
    )
  }

  const paper = data.paper || {}
  const versus = data.versus_backtest || {}
  const agents = data.agents || {}
  const checklist = data.checklist || {}
  const ran = paper.sessions || 0

  if (!ran) {
    return (
      <>
        <Head onReload={load} onCopy={copyReport} busy={busy} />
        <Panel title="Abhi shuru nahi hua">
          <Empty icon="flask">
            Koi session record nahi hua. Execution page par loop chalu karein —
            uske baad har cycle yahan likhega.
          </Empty>
          <KeyValues rows={[
            ['Guide', 'docs/12-paper-run.md'],
            ['Preflight', 'Setup page, ya python tools/preflight.py'],
          ]} />
        </Panel>
      </>
    )
  }

  const equity = sessions
    .filter((s) => s.closing_equity)
    .map((s) => ({ time: s.trade_date, value: s.closing_equity }))

  return (
    <>
      <Head onReload={load} onCopy={copyReport} busy={busy} />

      <div className="cols-2" style={{ marginBottom: 12 }}>
        <Hero
          label="Sessions record hue"
          value={fmtInt(ran)}
          tone={ran >= MIN_SESSIONS ? 'good' : ''}
          sub={ran >= MIN_SESSIONS
            ? `${paper.first_day} se ${paper.last_day}. Itna chal chuka hai ki numbers ka matlab bane.`
            : `${MIN_SESSIONS} chahiye (~2 mahine). Tab tak ke numbers abhi shor hain — ye page unhe dikhata hai, par un par faisla mat lein.`}
        />

        <Panel title="Ek nazar me" bodyClass="tight">
          <div className="stats" style={{ gridTemplateColumns: 'repeat(2, minmax(0,1fr))' }}>
            <Stat label="Return" value={`${paper.total_return_pct > 0 ? '+' : ''}${fmt(paper.total_return_pct)}%`}
                  colour={paper.total_return_pct > 0 ? 'up' : paper.total_return_pct < 0 ? 'down' : ''}
                  accent={paper.total_return_pct > 0 ? 'g' : paper.total_return_pct < 0 ? 'r' : ''} />
            <Stat label="Max drawdown" value={`${fmt(paper.max_drawdown_pct)}%`}
                  sub="intraday lows se" />
            <Stat label="Trades" value={fmtInt(paper.trades)}
                  sub={`${paper.wins ?? 0}W / ${paper.losses ?? 0}L`} />
            <Stat label="Win rate"
                  value={paper.win_rate_pct == null ? '—' : `${fmt(paper.win_rate_pct, 0)}%`} />
            <Stat label="Uptime" value={`${fmt(paper.uptime_pct, 1)}%`}
                  accent={paper.uptime_pct >= 95 ? 'g' : 'r'}
                  colour={paper.uptime_pct >= 95 ? 'up' : 'down'} />
            <Stat label="Flat days" value={fmtInt(paper.flat_days)}
                  sub={`${paper.days_with_halts ?? 0} din halt`} />
          </div>
        </Panel>
      </div>

      <Panel title="Equity curve"
             right={<span className="muted" style={{ fontSize: 11 }}>
               har session ka close
             </span>}
             bodyClass="tight" style={{ marginBottom: 12 }}>
        {equity.length < 2
          ? <Empty icon="chart">Curve ke liye kam se kam do session chahiye.</Empty>
          : <EquityChart equity={equity} theme={theme} height={260} />}
      </Panel>

      <div className="cols-2" style={{ marginBottom: 12 }}>
        <Panel title={versus.skipped ? 'Backtest se compare — skip' : 'Backtest se compare'}
               right={data.mode && (
                 <StatusTag tone={data.mode === 'system' ? 'ok' : 'good'} icon="info">
                   {data.mode} mode
                 </StatusTag>
               )}>
          {versus.skipped ? (
            <>
              <Note kind="">{versus.note}</Note>
              <p style={{ fontSize: 11.5, color: 'var(--text-2)', lineHeight: 1.6,
                          marginBottom: 0 }}>
                Strategy ka sawaal baad ka hai. Abhi ye sabit karna hai ki
                machinery roz chalti hai, order fill hote hain, har position
                par stop lagta hai, aur books match karte hain. Us par bharosa
                ho jaye to agli strategy usi machinery par chadhegi.
              </p>
            </>
          ) : !versus.ready ? (
            <Empty icon="flask">{versus.note}</Empty>
          ) : (
            <>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10,
                            marginBottom: 14 }}>
                {(versus.findings || []).length === 0 ? (
                  <StatusTag tone="good" icon="check">Koi bada gap nahi</StatusTag>
                ) : versus.findings.map((f) => {
                  const sev = SEVERITY[f.severity] || SEVERITY.warning
                  return (
                    <div key={f.check} style={{ display: 'flex', gap: 9,
                                                alignItems: 'flex-start' }}>
                      <StatusTag tone={sev.tone} icon={sev.icon}>{sev.label}</StatusTag>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: 12, color: 'var(--text-0)' }}>{f.detail}</div>
                        <div style={{ fontSize: 11.5, color: 'var(--text-2)', marginTop: 2 }}>
                          {f.likely_cause}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>

              <Meter label="Drawdown: paper vs backtest"
                     value={versus.paper?.max_drawdown_pct ?? 0}
                     limit={versus.baseline?.max_drawdown_pct || 1}
                     unit="%"
                     hint="Backtest se gehra drawdown sabse khatarnak gap hai — sizing ki galti capital ke saath badhti hai." />

              <div style={{ marginTop: 14 }}>
                <KeyValues rows={[
                  ['Baseline', versus.baseline?.label || 'unlabelled'],
                  ['Backtest return', `${fmt(versus.baseline?.total_return_pct)}%`],
                  ['Paper return', `${fmt(versus.paper?.total_return_pct)}%`],
                  ['Backtest trades', fmtInt(versus.baseline?.trades)],
                  ['Paper trades', fmtInt(versus.paper?.trades)],
                ]} />
              </div>

              <p style={{ fontSize: 11.5, color: 'var(--text-1)', lineHeight: 1.6,
                          marginTop: 12, marginBottom: 0 }}>
                {versus.verdict}
              </p>
            </>
          )}
        </Panel>

        <Panel title="Execution quality">
          <Meter label="Uptime" value={paper.uptime_pct ?? 0} limit={100} unit="%" invert
                 hint="Heartbeat ke gaps se ginti hai — timer crash ke dauraan bhi chalta rehta hai." />
          <Meter label="Order reject rate" value={paper.reject_rate_pct ?? 0}
                 limit={5} unit="%"
                 hint="5% se upar jaye to orders fill nahi ho rahe." />
          <div style={{ marginTop: 16 }}>
            <KeyValues rows={[
              ['Orders bheje', fmtInt(paper.orders_sent)],
              ['Reject hue', fmtInt(paper.orders_rejected)],
              ['Realised P&L', rs(paper.realised_pnl)],
              ['Halt wale din', fmtInt(paper.days_with_halts)],
              ['Jin din halt hua', (paper.halt_days || []).join(', ') || '—'],
            ]} />
          </div>
        </Panel>
      </div>

      <Panel title="Har agent kaam ka hai ya nahi"
             right={agents.ready && (
               <span className="muted" style={{ fontSize: 11 }}>
                 base rate {agents.base_rate_pct > 0 ? '+' : ''}{fmt(agents.base_rate_pct, 3)}%
                 {' '}per trade
               </span>
             )}
             bodyClass={agents.ready ? 'flush' : ''}
             style={{ marginBottom: 12 }}>
        {!agents.ready ? (
          <Empty icon="brain">{agents.note}</Empty>
        ) : (
          <>
            <Table cols={AGENT_COLS} rows={agents.agents}
                   empty="Koi agent record nahi" />
            <div style={{ padding: '12px 14px', borderTop: '1px solid var(--line)',
                          fontSize: 11.5, color: 'var(--text-2)', lineHeight: 1.6 }}>
              Edge <b>base rate ke against</b> napa jaata hai, zero ke against
              nahi — jis agent ki calls +0.4% deti hain wo bekaar hai agar sample
              ki har trade ne +0.4% diya. 20 se kam directional raay par koi
              number nahi dikhaya jaata.
            </div>
          </>
        )}
      </Panel>

      <Panel title="Go-live checklist"
             right={
               <StatusTag tone={checklist.machine_checks_clear ? 'good' : 'warn'}
                          icon={checklist.machine_checks_clear ? 'check' : 'alert'}>
                 {checklist.passed}/{checklist.total}
               </StatusTag>
             }>
        <div className="pf-section">
          {(checklist.items || []).map((item) => (
            <div className={'pf-row ' + (item.passed ? 'ok' : item.blocking ? 'bad' : 'warn')}
                 key={item.check}>
              <Icon name={item.passed ? 'check' : 'alert'} />
              <div className="pf-body">
                <div className="pf-name">
                  {pretty(item.check)}
                  {!item.blocking && <em> (non-blocking)</em>}
                </div>
                <div className="pf-detail">{item.detail}</div>
              </div>
            </div>
          ))}
        </div>

        <div style={{ marginTop: 16 }}>
          <div className="pf-title">Aapke faisle — system check nahi kar sakta</div>
          {(checklist.still_your_call || []).map((line) => (
            <div className="pf-row warn" key={line}>
              <Icon name="info" />
              <div className="pf-body"><div className="pf-name">{line}</div></div>
            </div>
          ))}
        </div>

        <p style={{ fontSize: 11.5, color: 'var(--text-2)', marginTop: 14,
                    marginBottom: 0, lineHeight: 1.6 }}>
          Ye page kabhi <b>"live jao"</b> nahi kehta. Jo system apna hi paper
          khud check kare wo saboot nahi hota — ye sirf batata hai ki kya sach
          hai aur kya abhi khula hai.
        </p>
      </Panel>
    </>
  )
}

function Head({ onReload, onCopy, busy }) {
  return (
    <div className="page-head">
      <div>
        <h1>Forward test</h1>
        <p>Paper run ne ab tak kya sabit kiya</p>
      </div>
      <div className="spacer" />
      <button className="btn ghost sm" onClick={onCopy} disabled={busy}>
        <Icon name="copy" />Report copy
      </button>
      <button className="btn ghost sm" onClick={onReload}>
        <Icon name="refresh" />Reload
      </button>
    </div>
  )
}
