/* The agent layer, read in the order it actually matters.
 *
 * The size multiplier leads, because it is the only number from this whole
 * layer that reaches a position. Everything below it explains that number:
 * the stance that produced it, the challenge to that stance, and the twelve
 * analysts underneath.
 *
 * The number is also the guarantee made visible -- it can never exceed 1.0,
 * so an agent can shrink a trade and has no way to enlarge one. Showing it
 * as a share of full size rather than as a bare decimal is what makes that
 * readable at a glance.
 */
import { useCallback, useEffect, useState } from 'react'
import { fmt, get, post } from '../api.js'
import Icon from '../components/Icon.jsx'
import {
  Empty, Hero, KeyValues, Meter, Panel, SkeletonRows,
  StatusTag, Strength,
} from '../components/ui.jsx'
import { useInterval } from '../hooks/index.js'
import { useUI } from '../components/Feedback.jsx'

/* view -> how it is shown. Never colour alone: each carries its own word. */
const VIEW_TONE = { bullish: 'good', bearish: 'crit', neutral: 'muted' }
const VIEW_ICON = { bullish: 'chart', bearish: 'alert', neutral: 'info' }

const STANCE_TONE = {
  risk_on: 'good', neutral: 'ok', risk_off: 'warn', insufficient_data: 'muted',
}
const STANCE_LABEL = {
  risk_on: 'Risk on', neutral: 'Neutral', risk_off: 'Risk off',
  insufficient_data: 'No stance',
}

const SYNTHESIS = new Set(['chief_strategist', 'red_team'])

const pretty = (name) =>
  name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

/* A multiplier is easier to judge as "what share of full size". */
function multiplierTone(m) {
  if (m >= 0.95) return 'good'
  if (m >= 0.5) return 'ok'
  if (m > 0) return 'warn'
  return 'crit'
}

export default function Agents() {
  const { toast } = useUI()
  const [views, setViews] = useState(null)
  const [status, setStatus] = useState({})
  const [feed, setFeed] = useState(null)
  const [schedule, setSchedule] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    try {
      const [v, s, f, sch] = await Promise.all([
        get('/agents/views'),
        get('/agents/status'),
        get('/agents/instruments').catch(() => null),
        get('/agents/schedule').catch(() => null),
      ])
      setViews(v); setStatus(s); setFeed(f); setSchedule(sch); setError(null)
    } catch (e) {
      setError(e.message)
      setViews((prev) => prev)        // keep the last good read on screen
    }
  }, [])

  useEffect(() => { load() }, [load])
  useInterval(load, 20000)

  const runAgent = async (name) => {
    setBusy(name)
    try {
      await post(`/agents/${name}/run`)
      toast(`${pretty(name)} chal gaya`, 'ok')
      await load()
    } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }

  const runSynthesis = async () => {
    setBusy('synthesis')
    try {
      const out = await post('/agents/synthesis/run')
      toast(`Stance: ${out.stance?.stance ?? 'none'} · size ×${fmt(out.size_multiplier)}`, 'ok')
      await load()
    } catch (e) { toast(e.message, 'err') }
    setBusy('')
  }

  if (!views) {
    return (
      <>
        <PageHead onReload={load} busy={busy} onSynthesis={runSynthesis} />
        <Panel><SkeletonRows n={6} /></Panel>
      </>
    )
  }

  const synthesis = views.synthesis || {}
  const stance = synthesis.stance || {}
  const challenge = synthesis.challenge || {}
  const multiplier = Number(synthesis.size_multiplier ?? 1)
  const analysts = Object.entries(views.views || {})
    .filter(([name]) => !SYNTHESIS.has(name))

  return (
    <>
      <PageHead onReload={load} busy={busy} onSynthesis={runSynthesis} />

      {error && (
        <div className="halt-banner" style={{ marginBottom: 14 }}>
          <Icon name="alert" />
          <div>
            <div className="halt-title">Agent data nahi mil raha</div>
            <div className="halt-why">{error} — neeche purana data dikh raha hai.</div>
          </div>
        </div>
      )}

      <div className="cols-2" style={{ marginBottom: 12 }}>
        <Hero
          label="Position size multiplier"
          value={`×${fmt(multiplier)}`}
          tone={multiplierTone(multiplier)}
          sub={
            multiplier >= 1
              ? 'Agents poori size par rok nahi laga rahe. Ye 1.0 se upar kabhi nahi jaata — agent risk ghata sakta hai, badha nahi.'
              : `Agents trade ko ${Math.round(multiplier * 100)}% size par rok rahe hain.`
          }
        />

        <Panel title="Aaj ka stance" bodyClass="tight">
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
            <StatusTag tone={STANCE_TONE[stance.stance] || 'muted'} icon="scale">
              {STANCE_LABEL[stance.stance] || 'No stance'}
            </StatusTag>
            <StatusTag tone={stance.risk_flag ? 'warn' : 'muted'}
                       icon={stance.risk_flag ? 'alert' : 'check'}>
              {stance.risk_flag ? 'Risk flagged' : 'No risk flag'}
            </StatusTag>
            <StatusTag tone={synthesis.agreed ? 'good' : 'warn'}
                       icon={synthesis.agreed ? 'check' : 'alert'}>
              {synthesis.agreed ? 'Red Team agrees' : 'Red Team objects'}
            </StatusTag>
          </div>

          <KeyValues rows={[
            ['Risk budget', stance.risk_budget || '—'],
            // Without a stance the schema default (100%) is not a decision
            // anyone made, so it is shown as absent rather than as a limit.
            ['Max exposure', stance.stance && stance.stance !== 'insufficient_data'
              && stance.max_exposure_pct != null
              ? `${fmt(stance.max_exposure_pct, 0)}%` : '—'],
            ['Analysts weighed', stance.analysts_used ?? 0],
            ['Allowed sectors', (stance.allowed_sectors || []).join(', ') || 'koi rok nahi'],
            ['Blocked sectors', (stance.blocked_sectors || []).join(', ') || 'none'],
          ]} />

          {stance.reasoning && (
            <p className="agent-why" style={{ marginTop: 10, WebkitLineClamp: 4 }}>
              {stance.reasoning}
            </p>
          )}
        </Panel>
      </div>

      <div className="cols-2" style={{ marginBottom: 12 }}>
        <Panel title="Red Team ke objections">
          {(challenge.objections || []).length === 0 ? (
            <Empty icon="check">
              {synthesis.agreed
                ? 'Koi objection nahi.'
                : 'Red Team ne abhi challenge nahi kiya — size aadhi rahegi.'}
            </Empty>
          ) : (
            <ul style={{ margin: 0, paddingLeft: 18, display: 'flex',
                         flexDirection: 'column', gap: 8 }}>
              {challenge.objections.map((o, i) => (
                <li key={i} style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--text-1)' }}>{o}</li>
              ))}
            </ul>
          )}
          <div className="meter" style={{ marginTop: 14 }}>
            <Meter label="Red Team ka suggested size" invert
                   value={Number(challenge.suggested_size_multiplier ?? 1) * 100}
                   limit={100} unit="%"
                   hint="1.0 se upar nahi ja sakta — Red Team sirf ghata sakta hai." />
          </div>
        </Panel>

        <Panel title="Live data pahunch raha hai?"
               right={feed && (
                 <StatusTag tone={feedTone(feed)} icon={feedTone(feed) === 'good' ? 'check' : 'alert'}>
                   {feed.receiving} / {feed.needed}
                 </StatusTag>
               )}>
          {!feed ? (
            <Empty icon="inbox">Subscription status nahi mila.</Empty>
          ) : (
            <>
              <Meter label="Instruments subscribed" invert
                     value={feed.subscribed} limit={feed.needed} />
              <Meter label="Instruments actually ticking" invert
                     value={feed.receiving} limit={feed.needed}
                     hint="Subscribe hona aur price aana do alag baatein hain — market band ho to ye zero rahega." />
              <div style={{ marginTop: 14 }}>
                <KeyValues rows={[
                  ['Full mode (depth + volume)', feed.by_mode?.full ?? 0],
                  ['LTPC mode (price + close)', feed.by_mode?.ltpc ?? 0],
                  ['Subscribe nahi hue', feed.not_subscribed_total ?? 0],
                ]} />
              </div>
            </>
          )}
        </Panel>
      </div>

      {schedule && !schedule.configured && (
        <div className="halt-banner paused" style={{ marginBottom: 12 }}>
          <Icon name="alert" />
          <div>
            <div className="halt-title">Agents apne aap nahi chal rahe</div>
            <div className="halt-why">
              Groq key nahi hai. Scheduler jaan-boojhkar kuch nahi chalata —
              warna har 15 minute me pandrah khali raayein store me bhar
              jaatin aur asli wale dab jaate. Key daalte hi apne aap shuru
              ho jayega.
            </div>
          </div>
        </div>
      )}

      {schedule && schedule.configured && (
        <Panel title="Schedule"
               right={<StatusTag tone={schedule.due_now.length ? 'ok' : 'good'}
                                 icon={schedule.due_now.length ? 'info' : 'check'}>
                 {schedule.due_now.length ? `${schedule.due_now.length} due` : 'sab current'}
               </StatusTag>}
               style={{ marginBottom: 12 }}>
          <p style={{ fontSize: 11.5, color: 'var(--text-2)', lineHeight: 1.6,
                      margin: '0 0 12px' }}>
            Har agent apne interval par khud chalta hai — loop har cycle me
            {' '}<b>{schedule.max_per_cycle}</b> tak chalata hai, sabse zyada
            late wale pehle. Run button sirf haath se dhakka dene ke liye hai,
            uspar kuch nirbhar nahi.
          </p>
          <KeyValues rows={[
            ['Cycles chale', fmtInt(schedule.cycles)],
            ['Agent runs', fmtInt(schedule.runs)],
            ['Abhi due', schedule.due_now.length ? schedule.due_now.map(pretty).join(', ') : 'koi nahi'],
            ['Pichhli baar chale', (schedule.last_agents || []).map(pretty).join(', ') || '—'],
            ['Analysts current', schedule.analysts_current ? 'haan — synthesis chal sakta hai' : 'nahi — synthesis ruka hai'],
          ]} />
        </Panel>
      )}

      <Panel title={`Analysts (${analysts.length})`}
             right={<span className="muted" style={{ fontSize: 11 }}>
               Har 20 second refresh
             </span>}>
        <div className="agent-grid">
          {analysts.map(([name, v]) => (
            <AgentCard key={name} name={name} view={v}
                       stat={status.agents?.[name]}
                       sched={(schedule?.agents || []).find((a) => a.agent === name)}
                       busy={busy === name}
                       onRun={() => runAgent(name)} />
          ))}
        </div>
      </Panel>
    </>
  )
}

function PageHead({ onReload, onSynthesis, busy }) {
  return (
    <div className="page-head">
      <div>
        <h1>Agents</h1>
        <p>Barah analysts, ek stance, aur us par Red Team ka hamla</p>
      </div>
      <div className="spacer" />
      <button className="btn ghost sm" onClick={onReload}>
        <Icon name="refresh" />Reload
      </button>
      <button className="btn primary sm" onClick={onSynthesis}
              disabled={busy === 'synthesis'}>
        {busy === 'synthesis' ? <i className="spin" /> : <Icon name="brain" />}
        Stance banao
      </button>
    </div>
  )
}

function AgentCard({ name, view, stat, sched, busy, onRun }) {
  const tone = VIEW_TONE[view.view] || 'muted'
  const confidence = Number(view.confidence ?? 0)
  const unhealthy = stat && stat.last_error
  const neverRun = !stat || !stat.calls

  return (
    <div className={'agent-card ' + (neverRun ? 'stale' : '')}>
      <div className="agent-top">
        <span className="agent-name">{pretty(name)}</span>
        <StatusTag tone={tone} icon={VIEW_ICON[view.view] || 'info'}>
          {view.view || 'neutral'}
        </StatusTag>
      </div>

      <Strength value={confidence}
                tone={confidence >= 0.55 ? (tone === 'muted' ? 'ok' : tone) : 'ok'} />
      <div className="agent-meta">
        <span className="agent-conf">confidence {fmt(confidence)}</span>
        {view.risk_flag
          ? <StatusTag tone="warn" icon="alert">risk</StatusTag>
          : <span className="agent-age">{scheduleNote(sched, neverRun)}</span>}
      </div>

      <p className="agent-why">{view.reasoning || 'Abhi koi raay nahi.'}</p>

      {unhealthy && (
        <StatusTag tone="crit" icon="alert">{String(stat.last_error).slice(0, 40)}</StatusTag>
      )}

      <button className="btn ghost sm" onClick={onRun} disabled={busy}>
        {busy ? <i className="spin" /> : <Icon name="play" />}Run
      </button>
    </div>
  )
}

/* What the card says instead of a bare "never run": when this agent is
   next due, so a quiet card reads as "waiting" rather than "broken". */
function scheduleNote(sched, neverRun) {
  if (!sched) return neverRun ? 'kabhi chala nahi' : 'ok'
  if (!sched.window_open) return `${sched.window} — abhi band`
  if (sched.due_now) return 'due'
  if (sched.overdue_seconds == null) return 'kabhi chala nahi'
  const mins = Math.max(0, Math.round(-sched.overdue_seconds / 60))
  return mins ? `${mins} min me` : 'abhi'
}

function feedTone(feed) {
  if (!feed.needed) return 'muted'
  if (feed.receiving >= feed.needed * 0.9) return 'good'
  if (feed.subscribed >= feed.needed) return 'warn'
  return 'crit'
}
