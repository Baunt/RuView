import type { Point } from './types';

export const clock = (t: number) => new Date(t * 1000).toLocaleTimeString('hu-HU', {hour: '2-digit', minute: '2-digit', second: '2-digit'});

export default function Timeline({points}: {points: Point[]}) {
  const usable = points.some(p => p.reference_hr !== null || p.ruview_hr !== null);
  if (!usable) return <div className="chart-empty"><div className="empty-lines" aria-hidden="true"/><strong>Még nincs összehasonlítható pulzusjel</strong><p>{points.length ? 'Importálj FIT-referenciát ehhez a felvételhez, és ellenőrizd a RuView számkiadási feltételeit.' : 'Indíts felvételt, majd rendeld hozzá az óra eredeti FIT-fájlját.'}</p></div>;
  const width = 1000, height = 260, left = 48, right = 18, top = 20, bottom = 40;
  const values = points.flatMap(p => [p.reference_hr, p.ruview_hr]).filter((n): n is number => n !== null);
  const lo = Math.floor(Math.min(40, ...values)/20)*20;
  const hi = Math.ceil(Math.max(120, ...values)/20)*20;
  const start = points[0].timestamp, end = points.at(-1)!.timestamp;
  const x = (t: number) => left+(t-start)/Math.max(1, end-start)*(width-left-right);
  const y = (n: number) => top+(hi-n)/(hi-lo)*(height-top-bottom);
  function paths(key: 'reference_hr' | 'ruview_hr') {
    const result: string[] = []; let current: Point[] = [];
    const flush = () => {
      if (current.length) {
        const step = Math.max(1, Math.ceil(current.length/900));
        result.push(current.filter((_, i) => i%step === 0 || i === current.length-1).map((p, i) => `${i ? 'L' : 'M'}${x(p.timestamp).toFixed(2)},${y(p[key]!).toFixed(2)}`).join(' '));
        current = [];
      }
    };
    for (const p of points) { if (p[key] === null) flush(); else current.push(p); }
    flush(); return result;
  }
  return <div className="chart-wrap"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Pulzus idősor: Garmin kék folytonos, RuView narancs szaggatott. A megszakítások hiányzó értékek.">
    {[0, 1, 2, 3, 4].map(i => {const value = lo+(hi-lo)*i/4; return <g key={i}><line x1={left} x2={width-right} y1={y(value)} y2={y(value)} className="gridline"/><text x={left-12} y={y(value)+4} textAnchor="end">{value}</text></g>;})}
    {[0, 1, 2, 3, 4].map(i => {const t = start+(end-start)*i/4; return <text key={i} x={x(t)} y={height-12} textAnchor={i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}>{clock(t)}</text>;})}
    {paths('reference_hr').map((d, i) => <path key={`g${i}`} d={d} className="garmin-line"/>)}
    {paths('ruview_hr').map((d, i) => <path key={`r${i}`} d={d} className="ruview-line"/>)}
    {(['reference_hr', 'ruview_hr'] as const).flatMap(key => points.flatMap((p, i) => p[key] !== null && (i === 0 || points[i-1][key] === null) && (i === points.length-1 || points[i+1][key] === null) ? [<circle key={`${key}${i}`} cx={x(p.timestamp)} cy={y(p[key]!)} r={3} fill={key === 'reference_hr' ? '#2449b9' : '#ac521d'}/>] : []))}
  </svg></div>;
}
