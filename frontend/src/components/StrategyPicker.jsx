/* Strategy dropdown plus the parameter inputs that strategy declares.
   The backend owns the parameter list, so adding a strategy there makes it
   appear here with no front-end change. */
import { useEffect } from 'react'
import { Field } from './ui.jsx'

export default function StrategyPicker({
  strategies, value, onChange, params, onParams, showDescription = true,
}) {
  const current = strategies.find((s) => s.key === value)

  // Reset parameters to the strategy's own defaults whenever it changes.
  useEffect(() => {
    if (!current) return
    const defaults = {}
    for (const p of current.params) defaults[p.name] = p.default
    onParams(defaults)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, strategies.length])

  return (
    <>
      <Field label="Strategy">
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          {strategies.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
        </select>
      </Field>

      {showDescription && current && (
        <p className="muted" style={{ fontSize: 12, margin: '-6px 0 12px' }}>{current.description}</p>
      )}

      <div className="cols-2">
        {current && current.params.map((p) => (
          <Field key={p.name} label={p.label}>
            <input
              type="number"
              value={params[p.name] ?? p.default}
              min={p.min ?? undefined}
              max={p.max ?? undefined}
              step={p.step ?? 1}
              onChange={(e) => onParams({ ...params, [p.name]: Number(e.target.value) })}
            />
          </Field>
        ))}
      </div>
    </>
  )
}
