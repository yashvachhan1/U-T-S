/* Tiny trend line built from the live price samples collected while the
   dashboard is open — no extra API calls. */
export default function Sparkline({ points, width = 54, height = 20 }) {
  if (!points || points.length < 3) return <svg className="spark" />

  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min || 1
  const step = width / (points.length - 1)

  const xy = points.map((p, i) => [i * step, height - ((p - min) / span) * (height - 3) - 1.5])
  const line = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
  const area = `${line} L${width} ${height} L0 ${height} Z`

  const colour = points[points.length - 1] >= points[0] ? 'var(--up)' : 'var(--down)'

  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <path className="fill" d={area} fill={colour} />
      <path d={line} stroke={colour} />
    </svg>
  )
}
