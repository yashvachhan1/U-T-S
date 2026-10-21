/* Small presentational building blocks shared by every view. */
import Icon from './Icon.jsx'

export function Panel({ title, right, children, bodyClass = '', bodyStyle, className = '' }) {
  return (
    <div className={'panel ' + className}>
      {title && (
        <div className="ph">
          <h3>{title}</h3>
          {right && <><div className="spacer" />{right}</>}
        </div>
      )}
      <div className={'pb ' + bodyClass} style={bodyStyle}>{children}</div>
    </div>
  )
}

export function Field({ label, children }) {
  return (
    <label className="f">
      <span className="lbl">{label}</span>
      {children}
    </label>
  )
}

export function Num({ label, value, onChange, ...rest }) {
  return (
    <Field label={label}>
      <input type="number" value={value} onChange={(e) => onChange(e.target.value)} {...rest} />
    </Field>
  )
}

export function Select({ label, value, onChange, options }) {
  return (
    <Field label={label}>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
      </select>
    </Field>
  )
}

export function Check({ checked, onChange, children, style }) {
  return (
    <label className="check" style={style}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <i className="box"><Icon name="check" /></i>
      <span className="txt">{children}</span>
    </label>
  )
}

export function Seg({ value, onChange, options, className = '' }) {
  return (
    <div className={'seg ' + className}>
      {options.map(([v, label]) => (
        <button key={v} data-side={v} className={v === value ? 'on' : ''} onClick={() => onChange(v)}>
          {label}
        </button>
      ))}
    </div>
  )
}

export function Pill({ kind = '', className = '', children }) {
  return <span className={`pill ${kind} ${className}`}><i className="dot" />{children}</span>
}

export function Note({ kind = '', children }) {
  const ico = kind === 'bad' || kind === 'warn' ? 'alert' : kind === 'good' ? 'check' : 'info'
  return <div className={'note ' + kind}><Icon name={ico} /><span>{children}</span></div>
}

export function Empty({ icon = 'inbox', children }) {
  return <div className="empty"><Icon name={icon} />{children}</div>
}

export function Stat({ label, value, accent = '', colour = '', sub }) {
  return (
    <div className={'stat ' + accent}>
      <div className="k">{label}</div>
      <div className={'v ' + colour}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}

/**
 * cols: [{ label, num?, cls?(row), get(row, index) }]
 * Renders the empty state inside the table so the panel never collapses.
 */
export function Table({ cols, rows, empty = 'Kuch nahi mila' }) {
  if (!rows || !rows.length) {
    return (
      <table className="tbl">
        <tbody><tr><td colSpan={cols.length || 1}><Empty>{empty}</Empty></td></tr></tbody>
      </table>
    )
  }
  return (
    <table className="tbl">
      <thead>
        <tr>{cols.map((c, i) => <th key={i} className={c.num ? 'num' : ''}>{c.label}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, ri) => (
          <tr key={ri}>
            {cols.map((c, ci) => (
              <td key={ci} className={(c.num ? 'num ' : '') + (c.cls ? c.cls(r) : '')}>
                {c.get(r, ri + 1)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Placeholder rows shown while a table is still loading. */
export function SkeletonRows({ n = 5 }) {
  return (
    <div style={{ padding: '6px 0' }}>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="skl skl-row" style={{ width: `${90 - i * 7}%` }} />
      ))}
    </div>
  )
}

export function SkeletonStats({ n = 6 }) {
  return (
    <>{Array.from({ length: n }, (_, i) => <div key={i} className="skl skl-stat" />)}</>
  )
}

/** Horizontal share-of-total bar with a legend. */
export function AllocationBar({ items }) {
  const total = items.reduce((a, b) => a + b.value, 0)
  if (!total) return null
  const palette = ['#5b8cff', '#8b7bff', '#0ecb81', '#f0a93b', '#f6465d', '#26c6da', '#9ccc65', '#ec407a']
  const rows = items.map((it, i) => ({ ...it, pct: (it.value / total) * 100, colour: palette[i % palette.length] }))
  return (
    <>
      <div className="alloc">
        {rows.map((r) => (
          <span key={r.label} style={{ width: `${r.pct}%`, background: r.colour }} title={r.label} />
        ))}
      </div>
      <div className="alloc-legend">
        {rows.map((r) => (
          <div className="it" key={r.label}>
            <i className="sw" style={{ background: r.colour }} />
            {r.label} <span className="pc">{r.pct.toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </>
  )
}

export function SideTag({ side }) {
  return <span className={'side-tag ' + side}>{side}</span>
}

export function Spinner() {
  return <i className="spin" />
}

/**
 * A single ratio against a limit.
 *
 * The fill carries severity and the track is a lighter step of the same
 * ramp, so the state reads across the whole bar rather than only where the
 * fill ends. The number is always written out: a bar answers "roughly how
 * full", and the only question that matters here is "how close to the
 * limit", which needs the figure.
 */
export function Meter({ label, value, limit, unit = '', hint, invert = false }) {
  const ratio = limit > 0 ? Math.max(0, Math.min(1.25, value / limit)) : 0
  const pct = Math.min(100, ratio * 100)

  /* Two kinds of bar share this component and they read in opposite
     directions. A *limit* meter is worse as it fills -- 80% of the daily
     loss budget is a warning. A *coverage* meter is better as it fills --
     532 of 532 instruments subscribed is the goal, and colouring that red
     because it "hit the limit" would train the reader to ignore red. */
  const level = invert
    ? (ratio >= 0.99 ? 'good' : ratio >= 0.8 ? 'ok' : ratio >= 0.5 ? 'warn' : 'crit')
    : (ratio >= 1 ? 'crit' : ratio >= 0.8 ? 'warn' : 'ok')

  return (
    <div className="meter">
      <div className="meter-top">
        <span className="meter-label">{label}</span>
        <span className={'meter-value ' + (level === 'good' ? 'ok' : level)}>
          {fmtMeter(value)}{unit}
          <em> / {fmtMeter(limit)}{unit}</em>
        </span>
      </div>
      <div className="meter-track">
        <i className={'meter-fill ' + level} style={{ width: `${pct}%` }} />
      </div>
      {hint && <div className="meter-hint">{hint}</div>}
    </div>
  )
}

const fmtMeter = (n) =>
  n === null || n === undefined || Number.isNaN(Number(n))
    ? '—'
    : Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 })

/**
 * The one number a view leads with. Exactly one per view.
 *
 * Proportional figures on purpose -- tabular-nums gives every digit the
 * width of a zero, which looks loose at this size.
 */
export function Hero({ label, value, tone = '', sub, children }) {
  return (
    <div className={'hero ' + tone}>
      <div className="hero-k">{label}</div>
      <div className="hero-v">{value}</div>
      {sub && <div className="hero-sub">{sub}</div>}
      {children}
    </div>
  )
}

/**
 * State, never by colour alone.
 *
 * Colour is a second channel here, not the only one: the label is always
 * written and an icon rides along, so the tag survives colourblindness,
 * greyscale printing and forced-colors mode.
 */
export function StatusTag({ tone = 'muted', icon, children, className = '' }) {
  return (
    <span className={`status-tag ${tone} ${className}`}>
      {icon && <Icon name={icon} />}
      <span>{children}</span>
    </span>
  )
}

/** A labelled confidence/0-1 strength bar, used in the agent cards. */
export function Strength({ value, tone = 'ok' }) {
  const pct = Math.max(0, Math.min(1, Number(value) || 0)) * 100
  return (
    <div className="strength" title={`${pct.toFixed(0)}%`}>
      <i className={'strength-fill ' + tone} style={{ width: `${pct}%` }} />
    </div>
  )
}

/** Key/value rows for a small detail panel. */
export function KeyValues({ rows }) {
  return (
    <div className="kvs">
      {rows.map(([k, v]) => (
        <div className="kv" key={k}>
          <span className="kv-k">{k}</span>
          <span className="kv-v">{v}</span>
        </div>
      ))}
    </div>
  )
}
