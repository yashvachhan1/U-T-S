import { useCallback, useEffect, useState } from 'react'
import { get } from '../api.js'
import Icon from '../components/Icon.jsx'
import { useUI } from '../components/Feedback.jsx'
import { Note, Panel, SkeletonRows, StatusTag } from '../components/ui.jsx'

const ENV_SAMPLE = `UPSTOX_API_KEY=aapka_api_key
UPSTOX_API_SECRET=aapka_api_secret
UPSTOX_REDIRECT_URI=http://127.0.0.1:8000/api/auth/callback
PAPER_TRADING=true`

export default function Setup({ status }) {
  const { toast } = useUI()
  const redirect = status.redirect_uri || '—'
  const [pre, setPre] = useState(null)
  const [checking, setChecking] = useState(false)

  const runPreflight = useCallback(async () => {
    setChecking(true)
    try { setPre(await get('/preflight')) }
    catch (e) { toast(e.message, 'err') }
    setChecking(false)
  }, [toast])

  useEffect(() => { runPreflight() }, [runPreflight])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(redirect)
      toast('Redirect URL copy ho gaya', 'ok')
    } catch { toast('Copy nahi hua — manually select karein', 'err') }
  }

  return (
    <>
      <div className="page-head">
        <div><h1>Setup</h1><p>Paper run shuru karne ke liye kya baaki hai</p></div>
        <div className="spacer" />
        <button className="btn ghost sm" onClick={runPreflight} disabled={checking}>
          {checking ? <i className="spin" /> : <Icon name="refresh" />}Dobara check karo
        </button>
      </div>

      <Preflight data={pre} />

      <Panel className="doc">
        <ol>
          <Step title="Upstox developer console kholein">
            Upstox account me login karke <b>account.upstox.com/developer/apps</b> par jayein.
          </Step>

          <Step title="New App banayein">
            App ka koi bhi naam de sakte hain, jaise <code className="inline">My Trading Desk</code>.
          </Step>

          <Step title="Redirect URL daalein">
            Bilkul yahi URL copy karke paste karein — ek bhi character alag nahi hona chahiye:
            <div className="copy-row">
              <code>{redirect}</code>
              <button className="btn ghost icon" onClick={copy} title="Copy"><Icon name="copy" /></button>
            </div>
          </Step>

          <Step title="API Key aur Secret copy karein">
            App save karte hi dono mil jayenge.
          </Step>

          <Step title=".env file bharein">
            Project folder me <code className="inline">.env</code> kholein aur ye values daalein:
            <pre>{ENV_SAMPLE}</pre>
          </Step>

          <Step title="Server restart karein aur login">
            Upar <b>Upstox Login</b> dabayein, phir ek baar <b>refresh</b> icon se instruments download karein.
          </Step>
        </ol>

        <div style={{ marginTop: 18 }}>
          <Note kind="warn">
            Upstox ka access token <b>roz subah 3:30 baje</b> expire ho jata hai. Har trading din ek baar
            dobara login karna padega — ye Upstox ki policy hai.
          </Note>
        </div>
        <div style={{ marginTop: 10 }}>
          <Note kind="bad">
            <b>PAPER_TRADING=true</b> rehte hue koi bhi order Upstox tak nahi jaata — sab local database
            me record hota hai. Asli paise se trade karne ke liye hi isko <b>false</b> karein.
          </Note>
        </div>
      </Panel>
    </>
  )
}

/* The same checks `python tools/preflight.py` runs, so the terminal and
   this page cannot disagree about what is outstanding. Every failing item
   carries the exact line that fixes it -- a check that says "this is
   wrong" without saying what to do is one that gets ignored. */
function Preflight({ data }) {
  if (!data) {
    return <Panel title="Preflight"><SkeletonRows n={4} /></Panel>
  }

  const blocking = data.blocking || []
  const warnings = data.warnings || []

  return (
    <Panel
      title="Preflight"
      className="preflight"
      right={
        <StatusTag tone={data.ready ? 'good' : 'crit'}
                   icon={data.ready ? 'check' : 'alert'}>
          {data.ready ? 'Shuru kar sakte hain'
            : `${blocking.length} cheez baaki`}
        </StatusTag>
      }
    >
      {data.ready && (
        <Note kind="good">
          Blocking kuch nahi. Feed start karein, phir Execution page par loop
          chalu karein. {warnings.length > 0
            && `${warnings.length} optional cheez abhi bhi baaki hai — niche dekh lein.`}
        </Note>
      )}

      {(data.sections || []).map((section) => (
        <div className="pf-section" key={section.title}>
          <div className="pf-title">{section.title}</div>
          {section.checks.map((c) => (
            <div className={'pf-row ' + (c.ok ? 'ok' : c.blocking ? 'bad' : 'warn')}
                 key={c.name}>
              <Icon name={c.ok ? 'check' : 'alert'} />
              <div className="pf-body">
                <div className="pf-name">
                  {c.name}
                  {!c.ok && !c.blocking && <em> (optional)</em>}
                </div>
                {c.detail && <div className="pf-detail">{c.detail}</div>}
                {!c.ok && c.note && <div className="pf-note">{c.note}</div>}
                {!c.ok && c.fix && <div className="pf-fix">{c.fix}</div>}
              </div>
            </div>
          ))}
        </div>
      ))}

      <Note kind="">
        Poora guide: <code className="inline">docs/12-paper-run.md</code>.
        Terminal me <code className="inline">python tools/preflight.py</code>
        {' '}se bhi yahi check chalta hai.
      </Note>
    </Panel>
  )
}

function Step({ title, children }) {
  return (
    <li>
      <div className="step-t">{title}</div>
      <div className="step-d">{children}</div>
    </li>
  )
}
