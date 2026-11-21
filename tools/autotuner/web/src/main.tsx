import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, ArrowDownToLine, ArrowRight, Check, Circle, Clock3, FileUp, FlaskConical, Radio, Settings2, ShieldCheck, Square, Timer, Wifi, X} from 'lucide-react';
import Timeline, {clock} from './Timeline';
import type {Block, Connection, Report, Session, Station} from './types';
import './style.css';

const labels: Record<string, string> = {seated: 'Nyugalom · ülve', supine: 'Nyugalom · fekve', motion: 'Mozgás', empty: 'Üres szoba', unmarked: 'Jelöletlen', sync: 'Időjel', note: 'Megjegyzés'};
const reasonLabels: Record<string, string> = {physical_source_unconfirmed: 'Fizikai forrás nincs igazolva', stale: 'Elavult adat', node_unavailable: 'Nincs friss CSI a node-tól', ambiguous_node: 'A forrás nem köthető egyetlen node-hoz', publication_abstained: 'A RuView nem ad ki hitelesített becslést', api_error: 'A RuView API nem érhető el', api_auth_required: 'A védett adatokhoz API-token szükséges', host_clock_discontinuity: 'A helyi óra megváltozott', no_heart_rate: 'Nincs pulzusbecslés', station_poll_stale: 'A mérőállomás adata elavult', slow_poll: 'Lassú API-válasz', adapter_error: 'Nem értelmezhető API-válasz', poll_gap: 'Felvételi kiesés'};
const num = (value: number | null | undefined, digits = 1) => value == null ? '—' : value.toLocaleString('hu-HU', {maximumFractionDigits: digits});
const percent = (value: number | null | undefined) => value == null ? '—' : `${num(value*100)}%`;
const date = (value: number) => new Date(value*1000).toLocaleString('hu-HU');

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : `Sikertelen kérés (${response.status}). Ellenőrizd a mezőket.`);
  }
  return response.json();
}
const post = (body?: unknown): RequestInit => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body ?? {})});

function App() {
  const [station, setStation] = useState<Station | null>(null);
  const [now, setNow] = useState(Date.now());
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState('');
  const [report, setReport] = useState<Report | null>(null);
  const [reportRevision, setReportRevision] = useState(0);
  const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState('');
  const [name, setName] = useState('Nyugalmi mérés'); const [block, setBlock] = useState<Block>('seated');
  const [offset, setOffset] = useState('0'); const [uncertainty, setUncertainty] = useState('');
  const [note, setNote] = useState('');
  const [connection, setConnection] = useState<Connection | null>(null); const [token, setToken] = useState('');
  const requestVersion = useRef(0);
  const session = sessions.find(s => s.id === selected);
  const refresh = async () => {
    const [status, list] = await Promise.all([api<Station>('/status'), api<Session[]>('/sessions')]);
    setStation(status); setSessions(list); setConnection(c => c ?? status.connection);
    setSelected(id => id || status.active_session_id || list[0]?.id || '');
  };
  useEffect(() => { void refresh().catch(e => setError(e.message)); const id = window.setInterval(() => { setNow(Date.now()); void refresh().catch(e => setError(e.message)); }, 2000); return () => window.clearInterval(id); }, []);
  useEffect(() => {
    const s = sessions.find(s => s.id === selected);
    setOffset(String(s?.alignment.offset_s ?? 0)); setUncertainty(s?.alignment.uncertainty_s == null ? '' : String(s.alignment.uncertainty_s));
    setReport(null);
  }, [selected]);
  useEffect(() => {
    const version = ++requestVersion.current;
    if (!selected) return;
    const load = () => api<Report>(`/sessions/${selected}/report`).then(r => {if (requestVersion.current === version) setReport(r);}).catch(e => {if (requestVersion.current === version) setError(e.message);});
    void load(); const id = window.setInterval(() => void load(), 3000);
    return () => {window.clearInterval(id); requestVersion.current++;};
  }, [selected, reportRevision]);
  async function act(label: string, action: () => Promise<unknown>, success = '') {
    setBusy(label); setError(''); setNotice('');
    try {await action(); await refresh(); setReportRevision(v => v+1); setNotice(success);} catch (e) {setError((e as Error).message);} finally {setBusy('');}
  }
  async function upload(kind: 'fit' | 'raw', file?: File) {
    if (!file || !selected) return;
    const form = new FormData(); form.append('file', file);
    await act('Importálás…', () => api(`/sessions/${selected}/${kind}`, {method: 'POST', body: form}), kind === 'fit' ? 'A FIT-fájl beolvasva és ellenőrzőösszeggel megőrizve.' : 'A nyers felvétel ellenőrizve és hozzárendelve. Az óraszinkron még ellenőrzendő.');
  }
  const cached = station?.latest;
  const latest = cached && now/1000-cached.received_at >= 4 ? {...cached, hr_bpm: null, br_rpm: null, status: 'unavailable', reasons: ['station_poll_stale']} : cached;
  const health = latest?.responses?.health;
  const calibration = latest && now/1000-latest.received_at < 4 ? latest.responses?.calibration : undefined;
  const calibrating = calibration?.active && ['collecting', 'uncalibrated'].includes(calibration.status ?? '');
  const calibrationNames: Record<string, string> = {none: 'Nincs kalibráció', collecting: 'Üres szoba mérése', uncalibrated: 'Mérés előkészítése', fresh: 'Friss kalibráció', stale: 'Elavult kalibráció', expired: 'Lejárt kalibráció', bootstrap: 'Tárolt háttérmodell'};
  const durationText = (seconds: number) => `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2, '0')}`;
  const live = health?.source === 'esp32' && health.processing?.state === 'live' && (health.processing.last_update_age_ms ?? Infinity) < 3000 && !!latest && Date.now()/1000-latest.received_at < 4;
  const recording = station?.active_session_id === selected && !!selected;
  const quality = report?.reference_quality;
  const statusReason = latest?.reasons.map(r => reasonLabels[r] ?? r).join(' · ');
  return <div className="shell">
    <aside className="sidebar">
      <a href="#station" className="brand"><span className="brand-mark"><Activity size={22}/></span><span>RuView<span className="brand-sub">AUTOTUNER</span></span></a>
      <p className="nav-caption">MUNKATERÜLET</p>
      <a href="#station" className="nav-link active"><Radio size={18}/> Mérőállomás <span className="nav-number">01</span></a>
      <a href="#recordings" className="nav-link"><Clock3 size={18}/> Felvételek <span className="nav-number">{sessions.length}</span></a>
      <a href="#connection" className="nav-link"><Settings2 size={18}/> Kapcsolat</a>
      <div className="sidebar-bottom"><FlaskConical size={20}/><strong>Először mérünk.</strong><p>A hangolás az ellenőrzött nyers felvételre és referenciára épül.</p><span className="version">Mérőállomás v0.1</span></div>
    </aside>
    <main id="station">
      <header className="topbar"><span>HELYI MÉRÉSI MUNKATERÜLET</span><span className={`badge ${live ? 'good' : 'neutral'}`}><span className="dot"/>{live ? 'RuView adatfolyam élő' : station ? 'Kapcsolat ellenőrzése' : 'Betöltés…'}</span></header>
      <div className="page-heading"><div><p className="eyebrow">01 / ADATGYŰJTÉS ÉS REFERENCIA</p><h1>Mérőállomás</h1><p>Rögzítsd a jelet. Illeszd a referenciát. Lásd az eltérést.</p></div><span className="mode"><ShieldCheck size={16}/> Megfigyelési mód</span></div>
      {error && <div className="alert error" role="alert"><strong>Nem sikerült: </strong>{error}<button aria-label="Hiba bezárása" onClick={() => setError('')}><X size={18}/></button></div>}
      {notice && <div className="alert success" role="status"><Check size={18}/>{notice}<button aria-label="Visszajelzés bezárása" onClick={() => setNotice('')}><X size={18}/></button></div>}
      {busy && <p className="busy" role="status">{busy}</p>}
      <section className="status-grid" aria-label="Mérési feltételek">
        <div className="status-card"><div className="card-label"><Wifi size={16}/> Szenzor</div><div className="status-value">ESP32 · node {latest?.node?.node_id ?? station?.connection.node_id ?? '—'}</div><p>{latest?.node ? `${num(latest.node.rssi_dbm, 0)} dBm · ${latest.node.csi_status === 'active' ? 'friss CSI' : 'elavult CSI'}` : station?.connection.token_set ? 'Node-adat ellenőrzése' : 'API-token szükséges a node-adathoz'}</p><span className={`status-line ${latest?.node?.csi_status === 'active' ? 'ok' : ''}`}/></div>
        <div className="status-card"><div className="card-label"><ShieldCheck size={16}/> Kalibráció</div><div className="status-value">{calibration?.status ? calibrationNames[calibration.status] ?? calibration.status : 'Nincs ellenőrizve'}</div>{calibrating ? <><p>{durationText(calibration?.elapsed_s ?? 0)} / {durationText(calibration?.min_duration_s ?? 0)} · {num(calibration?.frame_count, 0)} minta</p><progress className="calibration-progress" aria-label="Kalibráció időtartama" value={Math.min(calibration?.elapsed_s ?? 0, calibration?.min_duration_s || 1)} max={calibration?.min_duration_s || 1}/></> : <p>{latest?.responses?.vitals?.authority === 'explicit_calibration' ? 'A RuView publikációs kapuja teljesül' : 'A számkiadást a RuView feltételei szabályozzák'}</p>}<span className={`status-line ${calibration?.status === 'fresh' ? 'ok' : ''}`}/></div>
        <div className="status-card"><div className="card-label"><Activity size={16}/> Garmin-referencia</div><div className="status-value">{quality ? `${num(quality.valid_hr, 0)} pulzusrekord` : 'FIT-fájlra vár'}</div><p>{quality ? `Tényleges medián mintaköz: ${num(quality.median_interval_s)} s` : 'Eredeti tevékenységfájl importálható'}</p><span className={`status-line ${quality ? 'ok' : ''}`}/></div>
        <div className="status-card"><div className="card-label"><Radio size={16}/> Nyers I/Q-felvétel</div><div className="status-value">{session?.raw_capture ? `${num(session.raw_capture.raw_frames, 0)} keret` : 'Még nincs csatolva'}</div><p>{session?.raw_capture ? `Óraszinkron: ellenőrizetlen · hiány: ${session.raw_capture.missing_sequences}` : 'A későbbi pulzushangolás előfeltétele'}</p><span className={`status-line ${session?.raw_capture ? 'warn' : ''}`}/></div>
      </section>
      <section className="panel recording-panel" aria-labelledby="record-title"><div className="panel-heading"><div><p className="eyebrow">MÉRÉSI ALKALOM</p><h2 id="record-title">{recording ? 'Felvétel folyamatban' : 'Új felvétel előkészítése'}</h2></div><Timer size={22} className="muted"/></div>
        <div className="record-form"><label>Mérés neve<input value={name} onChange={e => setName(e.target.value)} maxLength={120} disabled={!!station?.active_session_id}/></label><label>Kezdő mérési blokk<select value={block} onChange={e => setBlock(e.target.value as Block)} disabled={!!station?.active_session_id}>{Object.entries(labels).filter(([k]) => ['seated','supine','motion','empty','unmarked'].includes(k)).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          {station?.active_session_id ? <button className="button stop" disabled={!!busy} onClick={() => void act('Felvétel lezárása…', () => api('/sessions/stop', post()), 'A felvétel lezárva, az adatok elmentve.')}><Square size={16}/> Felvétel leállítása</button> : <button className="button primary" disabled={!!busy || !station || !name.trim()} onClick={() => void act('Felvétel indítása…', async () => {const s = await api<Session>('/sessions/start', post({name, initial_block: block})); setSelected(s.id);}, 'A felvétel elindult. Az óra tevékenységét is indítsd el.')}><Circle size={16}/> Felvétel indítása</button>}
        </div><p className="helper">A felvétel az API megfigyeléseit menti. A nyers I/Q-adat külön collectorból csatolható. A Garmin órán indíts pulzust rögzítő tevékenységet.</p>
        <div className="live-readings" aria-label="RuView aktuális publikus értékei"><div><span>RuView pulzus</span><strong>{num(latest?.hr_bpm)} <small>ütés/perc</small></strong></div><div><span>RuView légzés</span><strong>{num(latest?.br_rpm)} <small>/perc</small></strong></div><div className="live-reason"><span>Jelenlegi állapot</span><p>{latest?.status === 'valid' ? `Publikus érték elérhető · fogadás: ${clock(latest.received_at)}` : statusReason || 'Állapot ellenőrzése…'}</p></div></div>
        {recording && <div className="markers"><span className="recording-dot"/> <strong>{session?.name}</strong><span className="muted">Kezdés: {session && clock(session.started_at)}</span><div className="marker-actions">{(['seated','supine','motion','empty','sync'] as const).map(kind => <button className="button small" key={kind} disabled={!!busy} onClick={() => void act('Esemény mentése…', () => api(`/sessions/${selected}/markers`, post({kind, note})), `${labels[kind]} esemény elmentve.`)}>{labels[kind]}</button>)}</div><label className="marker-note">Esemény megjegyzése<input value={note} onChange={e => setNote(e.target.value)} maxLength={500} placeholder="Például: az órán is megnyomtam a lap gombot"/></label></div>}
      </section>
      <div className="section-heading" id="recordings"><div><p className="eyebrow">FELVÉTEL ÉS ÖSSZEHASONLÍTÁS</p><h2>Egy közös időtengely</h2></div><label className="session-select"><span className="sr-only">Megnyitott felvétel</span><select value={selected} onChange={e => setSelected(e.target.value)} disabled={!sessions.length}>{!sessions.length && <option value="">Még nincs felvétel</option>}{sessions.map(s => <option key={s.id} value={s.id}>{s.name} · {date(s.started_at)}{s.status === 'recording' ? ' · RÖGZÍTÉS' : ''}</option>)}</select></label></div>
      <section className="panel timeline-panel"><div className="panel-heading"><div><h3>{session?.name ?? 'A mérésed itt jelenik meg'}</h3><p className="helper">Pulzus · ütés/perc · RuView: az API-válasz helyi fogadási ideje</p></div><div className="legend"><span><i className="legend-garmin"/> Garmin</span><span><i className="legend-ruview"/> RuView publikus</span></div></div>
        <Timeline points={report?.timeline ?? []}/>
        <div className="metrics"><div><span>Garmin-eltérés · MAE</span><strong>{num(report?.mae_bpm)} <small>ütés/perc</small></strong></div><div><span>Abszolút eltérés · P95</span><strong>{num(report?.p95_abs_bpm)} <small>ütés/perc</small></strong></div><div><span>Lefedettség · nyugalmi referencia</span><strong>{percent(report?.coverage)}</strong></div><div><span>Párosított / értékelhető idő</span><strong>{num(report?.counts.matched_seconds ?? 0, 0)} / {num(report?.counts.eligible_seconds ?? 0, 0)} <small>s</small></strong></div></div>
        <div className="chart-footnote"><span className="badge neutral">{report?.status === 'observational_only' ? 'Megfigyelési összehasonlítás' : 'Még nincs elegendő adat'}</span><span>A görbék megszakítása hiányzó adatot jelöl.</span></div>
      </section>
      <div className="bottom-grid"><section className="panel" aria-labelledby="reference-title"><div className="step-heading"><span className="step">1</span><div><h3 id="reference-title">Garmin FIT hozzáadása</h3><p>A mért pulzus referenciája</p></div><FileUp size={20}/></div><p className="body-copy">Garmin Connect → tevékenység → eredeti fájl exportálása. A ZIP-ből kicsomagolt <b>.fit</b> fájlt válaszd ki.</p><label className={`upload-zone ${!selected || busy ? 'disabled' : ''}`}><FileUp size={22}/><strong>{quality ? 'Másik FIT-fájl hozzárendelése' : 'FIT-fájl kiválasztása'}</strong><span>Legfeljebb 20 MB · az eredeti fájl megmarad</span><input aria-label="Garmin FIT-fájl" type="file" accept=".fit" disabled={!selected || !!busy} onChange={e => {void upload('fit', e.target.files?.[0]); e.target.value = '';}}/></label>{!selected && <p className="helper">Előbb indíts egy felvételt.</p>}
        {quality && <dl className="quality-list"><dt>Érvényes / összes rekord</dt><dd>{num(quality.valid_hr,0)} / {num(quality.records,0)}</dd><dt>Hiányzó / hibás pulzus</dt><dd>{num(quality.missing_hr,0)} / {num(quality.invalid_hr,0)}</dd><dt>Duplikált / ellentmondó</dt><dd>{num(quality.duplicates,0)} / {num(quality.conflicts,0)}</dd><dt>Időrendi visszalépés</dt><dd>{num(quality.out_of_order,0)}</dd><dt>Legnagyobb mintaköz</dt><dd>{num(quality.max_interval_s)} s</dd><dt>2 s-nál nagyobb rések</dt><dd>{num(quality.gaps_over_2s,0)}</dd></dl>}
      </section><section className="panel" aria-labelledby="align-title"><div className="step-heading"><span className="step">2</span><div><h3 id="align-title">Időillesztés rögzítése</h3><p>Egyetlen eltolás a teljes felvételre</p></div><Clock3 size={20}/></div><p className="body-copy">Pozitív eltolás későbbre helyezi a Garmin-adatot. Közös kezdő és záró időjel alapján állítsd be; az eltéréshez nem keresünk automatikusan jobb eltolást.</p><form onSubmit={e => {e.preventDefault(); void act('Időillesztés mentése…', () => api(`/sessions/${selected}/alignment`, post({offset_s: Number(offset), uncertainty_s: uncertainty === '' ? null : Number(uncertainty)})), 'A rögzített időillesztés elmentve.');}}><div className="two-fields"><label>Garmin-eltolás (s)<input type="number" step="0.1" min="-86400" max="86400" required value={offset} disabled={!selected} onChange={e => setOffset(e.target.value)}/></label><label>Bizonytalanság ± (s)<input type="number" step="0.1" min="0" max="3600" placeholder="Ismeretlen" value={uncertainty} disabled={!selected} onChange={e => setUncertainty(e.target.value)}/></label></div><button className="button" disabled={!selected || !!busy} type="submit">Illesztés mentése <ArrowRight size={16}/></button></form><p className="helper">Nincs interpoláció. Referencia-tolerancia: ±0,5 s. RuView-megfigyelés legnagyobb kora: 1,5 s. A Garmin belső késése ismeretlen.</p></section></div>
      {session && <section className="panel details-panel"><div className="panel-heading"><h3>Felvétel ellenőrzése és export</h3>{!recording && <a className="button" href={`/api/sessions/${selected}/export`}><ArrowDownToLine size={16}/> Mérés letöltése</a>}</div><p className="body-copy">{date(session.started_at)} → {session.ended_at ? date(session.ended_at) : 'folyamatban'} · {session.status === 'interrupted' ? 'Megszakadt felvétel' : recording ? 'Rögzítés' : 'Lezárva'}</p>{session.recovery_note && <p className="alert error">{session.recovery_note}</p>}<ul className="limitations">{report?.limitations.map(text => <li key={text}>{text}</li>)}</ul><p className="helper">Teljes idő: {num(report?.counts.total_seconds ?? 0,0)} s · Referenciával: {num(report?.counts.reference_seconds ?? 0,0)} s · RuView számmal: {num(report?.counts.estimate_seconds ?? 0,0)} s · Bias: {num(report?.bias_bpm)} ütés/perc</p><details><summary>Időbélyeges adatok és események</summary><p className="helper">Az utolsó 120 másodperc. A teljes sorozat a letöltött timeline.csv fájlban található.</p><div className="table-scroll"><table><thead><tr><th>Helyi idő</th><th>Garmin</th><th>RuView</th><th>Blokk / állapot</th></tr></thead><tbody>{report?.timeline.slice(-120).map(p => <tr key={p.timestamp}><td>{clock(p.timestamp)}</td><td>{num(p.reference_hr)}</td><td>{num(p.ruview_hr)}</td><td>{labels[p.block]} · {p.reason === 'valid' ? 'érték elérhető' : p.reason.split(',').map(r => reasonLabels[r] ?? r).join(', ')}</td></tr>)}</tbody></table></div>{report?.markers.map((m, i) => <p key={i} className="helper">{clock(m.timestamp)} · {labels[m.kind]} · {m.note}</p>)}</details><details><summary>Nyers I/Q-felvétel csatolása</summary><p className="body-copy">A collector a RuView-val azonos hálózati névtérben, a loopback UDP tee-n rögzít. Az API-felvétel nem tartalmazza ezt. A collector telepítését a projekt mérési útmutatója írja le.</p><label>Nyers felvétel (.jsonl.gz, max. 40 MB)<input type="file" accept=".gz" disabled={recording || !!busy} onChange={e => {void upload('raw', e.target.files?.[0]); e.target.value = '';}}/></label><p className="helper">A csatolás nem igazolja automatikusan a két gép óraszinkronját és a visszajátszó becslő egyezését.</p></details></section>}
      <section id="connection" className="panel connection-panel"><details><summary><Settings2 size={18}/> RuView-kapcsolat <span>{station?.connection.url ?? 'Betöltés…'}</span></summary>{connection && <form onSubmit={e => {e.preventDefault(); void act('Kapcsolat mentése…', async () => {await api('/connection', post({...connection, token: token || null})); setToken('');}, 'Kapcsolat elmentve. Az állapot néhány másodpercen belül frissül.');}}><div className="connection-fields"><label>RuView alapcíme<input type="url" required value={connection.url} disabled={!!station?.active_session_id} onChange={e => setConnection({...connection, url: e.target.value})}/></label><label>Helyi forrás-IP<input value={connection.source_address ?? ''} disabled={!!station?.active_session_id} onChange={e => setConnection({...connection, source_address: e.target.value || null})}/></label><label>Node ID<input type="number" min="0" max="255" value={connection.node_id} disabled={!!station?.active_session_id} onChange={e => setConnection({...connection, node_id: Number(e.target.value)})}/></label><label>API-token<input type="password" autoComplete="off" value={token} disabled={!!station?.active_session_id} placeholder={station?.connection.token_set ? 'Beállítva · üresen hagyva megmarad' : 'A RuView meglévő tokenje'} onChange={e => setToken(e.target.value)}/></label></div><p className="helper">A token csak az alkalmazás memóriájában él, fájlba és exportba nem kerül. Újraindítás után ismét megadható, vagy 0600 jogosultságú RUVIEW_TOKEN_FILE-ból betölthető.</p><button className="button" disabled={!!busy || !!station?.active_session_id}>Kapcsolat mentése</button></form>}</details>{latest?.status !== 'valid' && statusReason && <p className="helper connection-reason">{statusReason}</p>}</section>
      <footer>RuView AutoTuner <span>Helyi adattárolás · Rögzített időillesztés · Ellenőrizhető eredet</span></footer>
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
