/* lightweight-charts wrappers.
   The charts draw to canvas, so every theme colour has to be read back out of
   the stylesheet and pushed in whenever the theme flips. */
import { createChart } from 'lightweight-charts'
import { useEffect, useRef } from 'react'

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim()

function baseOpts(theme) {
  const grid = theme === 'light' ? 'rgba(207,216,229,.65)' : 'rgba(39,50,74,.4)'
  return {
    layout: {
      background: { color: 'transparent' },
      textColor: cssVar('--text-2'),
      fontFamily: 'JetBrains Mono, monospace',
      fontSize: 10,
    },
    grid: { vertLines: { color: grid }, horzLines: { color: grid } },
    rightPriceScale: { borderColor: cssVar('--line') },
    timeScale: { borderColor: cssVar('--line'), timeVisible: true, secondsVisible: false },
    crosshair: {
      mode: 0,
      vertLine: { color: cssVar('--line-2'), labelBackgroundColor: cssVar('--accent') },
      horzLine: { color: cssVar('--line-2'), labelBackgroundColor: cssVar('--accent') },
    },
  }
}

const toSec = (ts) => Math.floor(new Date(ts).getTime() / 1000)

/** lightweight-charts rejects duplicate or unsorted timestamps. */
function dedupe(rows) {
  const out = []
  let last = -1
  for (const r of rows) if (r.time > last) { out.push(r); last = r.time }
  return out
}

const volColour = (c, theme) =>
  c.close >= c.open
    ? (theme === 'light' ? 'rgba(8,154,102,.25)' : 'rgba(14,203,129,.28)')
    : (theme === 'light' ? 'rgba(217,48,72,.25)' : 'rgba(246,70,93,.28)')

/* ------------------------------------------------------------ price chart */
export function PriceChart({ candles, theme, onHover, markers, height }) {
  const host = useRef(null)
  const api = useRef(null)

  useEffect(() => {
    const chart = createChart(host.current, { ...baseOpts(theme), height: host.current.clientHeight || 420 })
    const candle = chart.addCandlestickSeries({})
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.86, bottom: 0 } })
    api.current = { chart, candle, volume }

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: host.current.clientWidth, height: host.current.clientHeight })
    })
    ro.observe(host.current)
    return () => { ro.disconnect(); chart.remove(); api.current = null }
    // Created once; theme and data are pushed in by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // crosshair -> OHLC readout
  useEffect(() => {
    if (!api.current || !onHover) return
    const { chart, candle } = api.current
    const handler = (p) => onHover((p.seriesData && p.seriesData.get(candle)) || null)
    chart.subscribeCrosshairMove(handler)
    return () => chart.unsubscribeCrosshairMove(handler)
  }, [onHover])

  // theme
  useEffect(() => {
    if (!api.current) return
    const { chart, candle } = api.current
    const up = cssVar('--up'), down = cssVar('--down')
    chart.applyOptions(baseOpts(theme))
    candle.applyOptions({
      upColor: up, downColor: down,
      borderUpColor: up, borderDownColor: down,
      wickUpColor: up, wickDownColor: down,
    })
  }, [theme])

  // data
  useEffect(() => {
    if (!api.current || !candles) return
    const { chart, candle, volume } = api.current
    candle.setData(dedupe(candles.map((c) => ({
      time: toSec(c.ts), open: c.open, high: c.high, low: c.low, close: c.close }))))
    volume.setData(dedupe(candles.map((c) => ({
      time: toSec(c.ts), value: c.volume, color: volColour(c, theme) }))))
    chart.timeScale().fitContent()
  }, [candles, theme])

  // Entry / exit arrows for backtest results.
  useEffect(() => {
    if (!api.current) return
    api.current.candle.setMarkers(
      (markers || []).map((m) => ({
        time: toSec(m.time),
        position: m.side === 'BUY' ? 'belowBar' : 'aboveBar',
        color: m.side === 'BUY' ? cssVar('--up') : cssVar('--down'),
        shape: m.side === 'BUY' ? 'arrowUp' : 'arrowDown',
        text: m.text || '',
      })))
  }, [markers, theme])

  return <div className="chart-host" ref={host} style={height ? { height, flex: 'none' } : undefined} />
}

/* ----------------------------------------------------------- equity curve */
export function EquityChart({ equity, theme }) {
  const host = useRef(null)
  const api = useRef(null)

  useEffect(() => {
    const chart = createChart(host.current, { ...baseOpts(theme), height: 280 })
    const area = chart.addAreaSeries({
      lineColor: cssVar('--accent'),
      topColor: 'rgba(91,140,255,.28)',
      bottomColor: 'rgba(91,140,255,.01)',
      lineWidth: 2,
    })
    api.current = { chart, area }
    const ro = new ResizeObserver(() => chart.applyOptions({ width: host.current.clientWidth }))
    ro.observe(host.current)
    return () => { ro.disconnect(); chart.remove(); api.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!api.current) return
    api.current.chart.applyOptions(baseOpts(theme))
    api.current.area.applyOptions({ lineColor: cssVar('--accent') })
  }, [theme])

  useEffect(() => {
    if (!api.current || !equity) return
    api.current.area.setData(dedupe(equity.map((e) => ({ time: toSec(e.time), value: e.value }))))
    api.current.chart.timeScale().fitContent()
  }, [equity])

  return <div id="equityChart" ref={host} />
}
