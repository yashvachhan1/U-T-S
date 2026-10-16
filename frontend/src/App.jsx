import { useCallback, useEffect, useState } from 'react'
import { get, post } from './api.js'
import Icon from './components/Icon.jsx'
import CommandPalette from './components/CommandPalette.jsx'
import { Pill } from './components/ui.jsx'
import { useUI } from './components/Feedback.jsx'
import { useClock, useInterval, useTheme } from './hooks/index.js'
import ChartTrade from './views/ChartTrade.jsx'
import { OrdersView, PortfolioView } from './views/OrdersPortfolio.jsx'
import Backtest from './views/Backtest.jsx'
import Bots from './views/Bots.jsx'
import Setup from './views/Setup.jsx'

const NAV = [
  ['Trade', [
    ['chart', 'Chart & Trade', 'chart'],
    ['orders', 'Orders', 'list'],
    ['portfolio', 'Portfolio', 'wallet'],
  ]],
  ['Strategy', [
    ['backtest', 'Backtest', 'flask'],
    ['bots', 'Algo Bots', 'bot'],
  ]],
]

const ALL_VIEWS = [
  ...NAV.flatMap(([, items]) => items.map(([id, label, icon]) => ({ id, label, icon }))),
  { id: 'help', label: 'Setup', icon: 'cog' },
]

export default function App() {
  const { toast, confirm } = useUI()
  const [theme, toggleTheme] = useTheme()
  const clock = useClock()

  const [view, setView] = useState('chart')
  const [status, setStatus] = useState({})
  const [strategies, setStrategies] = useState([])
  const [refreshing, setRefreshing] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [pendingInstrument, setPendingInstrument] = useState(null)

  const loadStatus = useCallback(async () => {
    try { setStatus(await get('/status')) }
    catch { setStatus((s) => ({ ...s, serverDown: true })) }
  }, [])

  useEffect(() => {
    loadStatus()
    get('/strategies').then(setStrategies).catch((e) => toast(e.message, 'err'))
  }, [loadStatus, toast])

  useInterval(loadStatus, 20000)

  /* Ctrl/Cmd+K opens the palette; Alt+1..6 jump straight to a view. */
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((o) => !o)
        return
      }
      if (e.altKey && !e.ctrlKey && !e.metaKey) {
        const i = Number(e.key)
        if (i >= 1 && i <= ALL_VIEWS.length) {
          e.preventDefault()
          setView(ALL_VIEWS[i - 1].id)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const openInstrument = useCallback((row) => {
    setView('chart')
    setPendingInstrument(row)
  }, [])

  /* ------------------------------------------------------- actions */
  const login = async () => {
    try {
      const { url } = await get('/auth/login')
      const win = window.open(url, 'upstox-login', 'width=520,height=740')
      const timer = setInterval(async () => {
        if (win && win.closed) {
          clearInterval(timer)
          const s = await get('/status').catch(() => null)
          setStatus(s || {})
          if (s?.logged_in) toast('Upstox connected', 'ok')
        }
      }, 800)
    } catch (e) { toast(e.message, 'err') }
  }

  const logout = async () => {
    const ok = await confirm({
      title: 'Logout karein?',
      note: 'Chal rahe saare bots band ho jayenge.',
    })
    if (!ok) return
    await post('/auth/logout')
    toast('Logout ho gaya')
    loadStatus()
  }

  const refreshInstruments = async () => {
    setRefreshing(true)
    try {
      const r = await post('/instruments/refresh')
      toast(`${Number(r.count).toLocaleString('en-IN')} instruments load ho gaye`, 'ok')
    } catch (e) { toast(e.message, 'err') }
    setRefreshing(false)
    loadStatus()
  }

  /* ------------------------------------------------------------ ui */
  return (
    <div className="app">

      <div className="brand">
        <div className="mark"><Icon name="logo" style={{ color: '#fff', width: 16, height: 16 }} /></div>
        <div className="name">Trading<em>Desk</em></div>
      </div>

      <header className="topbar">
        <Pill kind={status.paper_trading === false ? 'bad live' : 'ok'}>
          {status.paper_trading === false ? 'LIVE MONEY' : 'PAPER'}
        </Pill>
        <Pill className="t-conn" kind={status.serverDown ? 'bad' : status.logged_in ? 'info' : ''}>
          {status.serverDown ? 'Server down' : status.logged_in ? 'Connected' : 'Disconnected'}
        </Pill>
        <Pill className="t-market" kind={status.market_open ? 'ok' : ''}>
          Market {status.market_open ? 'open' : 'closed'}
        </Pill>
        <div className="sep" />
        <div className="clock">{clock}&nbsp;<b>IST</b></div>

        <div className="spacer" />

        {status.logged_in && (
          <div className="who">
            <div className="n">{status.user_name}</div>
            <div className="e">{status.user_id}</div>
          </div>
        )}

        <button className="kbd-hint" onClick={() => setPaletteOpen(true)} title="Command palette">
          <Icon name="search" style={{ width: 12, height: 12 }} />
          Search <kbd>Ctrl</kbd><kbd>K</kbd>
        </button>
        <button className="btn ghost icon" onClick={toggleTheme} title="Light / dark theme">
          <Icon name={theme === 'light' ? 'sun' : 'moon'} />
        </button>
        <button className="btn ghost icon" onClick={refreshInstruments} disabled={refreshing}
                title="Instrument list dobara download karein">
          {refreshing ? <i className="spin" /> : <Icon name="refresh" />}
        </button>
        {status.logged_in
          ? <button className="btn ghost icon" onClick={logout} title="Logout"><Icon name="power" /></button>
          : <button className="btn primary" onClick={login}><Icon name="plug" />Upstox Login</button>}
      </header>

      <nav className="rail">
        {NAV.map(([group, items]) => (
          <div key={group} style={{ display: 'contents' }}>
            <div className="grp">{group}</div>
            {items.map(([id, label, icon]) => (
              <button key={id} className={'rail-item ' + (view === id ? 'active' : '')}
                      onClick={() => setView(id)}>
                <Icon name={icon} />
                <span>{label}</span>
                {id === 'bots' && status.active_bots > 0 && <i className="tag">{status.active_bots}</i>}
              </button>
            ))}
          </div>
        ))}
        <div className="rail-foot">
          <button className={'rail-item ' + (view === 'help' ? 'active' : '')}
                  onClick={() => setView('help')}>
            <Icon name="cog" /><span>Setup</span>
          </button>
        </div>
      </nav>

      <main>
        <section className={'view flush fill ' + (view === 'chart' ? 'active' : '')}>
          {view === 'chart' && (
            <ChartTrade
              status={status} theme={theme} onLogin={login}
              pending={pendingInstrument}
              onPendingHandled={() => setPendingInstrument(null)}
            />
          )}
        </section>
        <section className={'view ' + (view === 'orders' ? 'active' : '')}>
          {view === 'orders' && <OrdersView />}
        </section>
        <section className={'view ' + (view === 'portfolio' ? 'active' : '')}>
          {view === 'portfolio' && <PortfolioView />}
        </section>
        <section className={'view ' + (view === 'backtest' ? 'active' : '')}>
          {view === 'backtest' && <Backtest strategies={strategies} theme={theme} />}
        </section>
        <section className={'view ' + (view === 'bots' ? 'active' : '')}>
          {view === 'bots' && <Bots strategies={strategies} status={status} />}
        </section>
        <section className={'view ' + (view === 'help' ? 'active' : '')}>
          {view === 'help' && <Setup status={status} />}
        </section>
      </main>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        views={ALL_VIEWS}
        onView={setView}
        onInstrument={openInstrument}
      />
    </div>
  )
}
