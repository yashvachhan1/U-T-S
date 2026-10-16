import Icon from '../components/Icon.jsx'
import { useUI } from '../components/Feedback.jsx'
import { Note, Panel } from '../components/ui.jsx'

const ENV_SAMPLE = `UPSTOX_API_KEY=aapka_api_key
UPSTOX_API_SECRET=aapka_api_secret
UPSTOX_REDIRECT_URI=http://127.0.0.1:8000/api/auth/callback
PAPER_TRADING=true`

export default function Setup({ status }) {
  const { toast } = useUI()
  const redirect = status.redirect_uri || '—'

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(redirect)
      toast('Redirect URL copy ho gaya', 'ok')
    } catch { toast('Copy nahi hua — manually select karein', 'err') }
  }

  return (
    <>
      <div className="page-head"><div><h1>Setup</h1><p>Upstox se connect karne ke steps</p></div></div>

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

function Step({ title, children }) {
  return (
    <li>
      <div className="step-t">{title}</div>
      <div className="step-d">{children}</div>
    </li>
  )
}
