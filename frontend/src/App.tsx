import React from 'react';
import {
  Activity,
  ArrowRightLeft,
  Banknote,
  FileUp,
  Pause,
  Play,
  RadioTower,
  RotateCcw,
  Save,
  ShieldCheck,
  SkipForward,
  SlidersHorizontal,
  Upload,
} from 'lucide-react';
import { api, type SimulationConfig, type SimulationSnapshot } from './api';

const emptyCsv = 'timestamp,symbol,open,high,low,close,volume\n';

function money(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 'Unavailable';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(number);
}

function number(value: unknown, digits = 2) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 'Unavailable';
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(parsed);
}

function isoMinute() {
  return Math.floor(Date.now() / 60000);
}

export function App() {
  const [snapshot, setSnapshot] = React.useState<SimulationSnapshot | null>(null);
  const [configDraft, setConfigDraft] = React.useState<SimulationConfig | null>(null);
  const [csvName, setCsvName] = React.useState('Recorded market day');
  const [csvText, setCsvText] = React.useState(emptyCsv);
  const [selectedSession, setSelectedSession] = React.useState('');
  const [speed, setSpeed] = React.useState(30);
  const [loop, setLoop] = React.useState(false);
  const [symbol, setSymbol] = React.useState('SPY');
  const [action, setAction] = React.useState('buy');
  const [confidence, setConfidence] = React.useState(0.9);
  const [trail, setTrail] = React.useState(2);
  const [status, setStatus] = React.useState('Idle');
  const [error, setError] = React.useState('');

  const refresh = React.useCallback(async () => {
    const next = await api.state();
    setSnapshot(next);
    setConfigDraft((current) => current ?? next.config);
    if (!selectedSession && next.sessions[0]) setSelectedSession(next.sessions[0].session_id);
  }, [selectedSession]);

  React.useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : String(err)));
    const id = window.setInterval(() => {
      refresh().catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(id);
  }, [refresh]);

  async function run<T>(label: string, fn: () => Promise<T>) {
    setError('');
    setStatus(label);
    try {
      await fn();
      await refresh();
      setStatus('Idle');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus('Error');
    }
  }

  async function loadFile(file: File | null) {
    if (!file) return;
    setCsvName(file.name.replace(/\.[^.]+$/, ''));
    setCsvText(await file.text());
  }

  function updateConfig<K extends keyof SimulationConfig>(key: K, value: SimulationConfig[K]) {
    setConfigDraft((current) => (current ? { ...current, [key]: value } : current));
  }

  function handoffPayload() {
    const normalized = symbol.trim().toUpperCase() || 'SPY';
    const stopType = action.includes('trailing') ? 'trailing' : action === 'regular_stop' ? 'regular' : undefined;
    return {
      contract_version: 'edge.pulse.handoff.v1',
      symbol: normalized,
      action,
      confidence,
      reason: 'operator simulation control',
      mode: 'paper',
      orb_session: 'market_open',
      stop_type: stopType,
      trailing_percent: stopType === 'trailing' ? trail : undefined,
      idempotency_key: `edge:${normalized}:${action}:market_open:${isoMinute()}:ui`,
      source: 'sentinel_edge',
      created_at: Date.now() / 1000,
      metadata: {},
    };
  }

  const positions = Object.values(snapshot?.account.positions ?? {});

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span>Sentinel</span>
          <strong>Simulation Engine</strong>
        </div>
        <div className="top-actions">
          <Badge tone={snapshot?.replay.active ? 'good' : 'neutral'} label={snapshot?.replay.active ? 'Replay active' : 'Replay stopped'} />
          <Badge tone={error ? 'bad' : status === 'Idle' ? 'good' : 'warn'} label={error || status} />
          <button type="button" onClick={() => run('Refreshing', refresh)} title="Refresh">
            <RadioTower size={16} />
          </button>
        </div>
      </header>

      <main className="grid">
        <section className="metric-row">
          <Metric icon={<Banknote size={18} />} label="Equity" value={money(snapshot?.account.total_equity)} sub={`Cash ${money(snapshot?.account.cash)}`} />
          <Metric icon={<Activity size={18} />} label="Replay Index" value={number(snapshot?.replay.index, 0)} sub={snapshot?.replay.current_timestamp || 'No timestamp'} />
          <Metric icon={<ShieldCheck size={18} />} label="Open Positions" value={number(snapshot?.account.open_positions, 0)} sub={`PnL ${money(snapshot?.account.day_pnl_dollar)}`} />
          <Metric icon={<ArrowRightLeft size={18} />} label="Current Prices" value={number(Object.keys(snapshot?.current_prices ?? {}).length, 0)} sub={Object.keys(snapshot?.current_prices ?? {}).join(', ') || 'No symbols'} />
        </section>

        <section className="panel tall">
          <PanelHeader icon={<SlidersHorizontal size={16} />} title="Execution Model" />
          {configDraft ? (
            <div className="form-grid">
              <NumberField label="Starting cash" value={configDraft.starting_cash} onChange={(value) => updateConfig('starting_cash', value)} />
              <NumberField label="Default quantity" value={configDraft.default_quantity} onChange={(value) => updateConfig('default_quantity', value)} />
              <NumberField label="Max allocation %" value={configDraft.max_allocation_pct} onChange={(value) => updateConfig('max_allocation_pct', value)} />
              <NumberField label="Fill ratio" value={configDraft.fill_ratio} step={0.05} onChange={(value) => updateConfig('fill_ratio', value)} />
              <NumberField label="Slippage bps" value={configDraft.slippage_bps} onChange={(value) => updateConfig('slippage_bps', value)} />
              <NumberField label="Commission" value={configDraft.commission_per_order} onChange={(value) => updateConfig('commission_per_order', value)} />
              <NumberField label="Reject below" value={configDraft.reject_below_confidence} step={0.05} onChange={(value) => updateConfig('reject_below_confidence', value)} />
              <NumberField label="Trail %" value={configDraft.default_trailing_percent} onChange={(value) => updateConfig('default_trailing_percent', value)} />
              <NumberField label="Stop %" value={configDraft.regular_stop_percent} onChange={(value) => updateConfig('regular_stop_percent', value)} />
              <NumberField label="Target %" value={configDraft.take_profit_percent} onChange={(value) => updateConfig('take_profit_percent', value)} />
              <button className="primary wide" type="button" onClick={() => run('Saving config', () => api.updateConfig(configDraft))}>
                <Save size={15} />
                Save Model
              </button>
            </div>
          ) : null}
        </section>

        <section className="panel tall">
          <PanelHeader icon={<FileUp size={16} />} title="Market Day Replay" />
          <div className="stack">
            <label className="field">
              <span>Session name</span>
              <input value={csvName} onChange={(event) => setCsvName(event.target.value)} />
            </label>
            <label className="file-button">
              <Upload size={15} />
              Load CSV
              <input type="file" accept=".csv,text/csv" onChange={(event) => loadFile(event.target.files?.[0] ?? null)} />
            </label>
            <textarea value={csvText} onChange={(event) => setCsvText(event.target.value)} spellCheck={false} />
            <button className="primary" type="button" onClick={() => run('Importing CSV', () => api.importCsv(csvName, csvText))}>
              <Upload size={15} />
              Import Bars
            </button>
            <div className="session-list">
              {(snapshot?.sessions ?? []).map((session) => (
                <button type="button" className={selectedSession === session.session_id ? 'selected' : ''} key={session.session_id} onClick={() => setSelectedSession(session.session_id)}>
                  <strong>{session.name}</strong>
                  <span>{session.symbols.join(', ')} / {session.bar_count} bars</span>
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="panel">
          <PanelHeader icon={<Play size={16} />} title="Playback" />
          <div className="form-grid compact">
            <NumberField label="Speed" value={speed} onChange={setSpeed} />
            <label className="check">
              <input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} />
              <span>Loop</span>
            </label>
            <button type="button" onClick={() => selectedSession && run('Starting replay', () => api.startReplay(selectedSession, speed, loop))}>
              <Play size={15} />
              Start
            </button>
            <button type="button" onClick={() => run('Stepping replay', api.stepReplay)}>
              <SkipForward size={15} />
              Step
            </button>
            <button type="button" onClick={() => run('Stopping replay', api.stopReplay)}>
              <Pause size={15} />
              Stop
            </button>
          </div>
        </section>

        <section className="panel">
          <PanelHeader icon={<ArrowRightLeft size={16} />} title="Handoff Composer" />
          <div className="form-grid compact">
            <label className="field">
              <span>Symbol</span>
              <input value={symbol} onChange={(event) => setSymbol(event.target.value)} />
            </label>
            <label className="field">
              <span>Action</span>
              <select value={action} onChange={(event) => setAction(event.target.value)}>
                {['buy', 'sell', 'trailing_stop', 'opening_trailing_stop', 'tighten_trailing_stop', 'regular_stop', 'stop_all', 'emergency_exit', 'dca', 'stop_buying'].map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            </label>
            <NumberField label="Confidence" value={confidence} step={0.05} onChange={setConfidence} />
            <NumberField label="Trail %" value={trail} onChange={setTrail} />
            <button className="primary wide" type="button" onClick={() => run('Sending handoff', () => api.handoff(handoffPayload()))}>
              <ArrowRightLeft size={15} />
              Send Handoff
            </button>
          </div>
        </section>

        <section className="panel wide-panel">
          <PanelHeader icon={<Banknote size={16} />} title="Positions" />
          <div className="table">
            <div className="row head">
              <span>Symbol</span><span>Qty</span><span>Entry</span><span>Price</span><span>PnL</span><span>Trail</span>
            </div>
            {positions.length ? positions.map((position) => (
              <div className="row" key={position.symbol}>
                <span>{position.symbol}</span>
                <span>{number(position.quantity)}</span>
                <span>{money(position.avg_entry)}</span>
                <span>{money(position.current_price)}</span>
                <span className={position.pnl >= 0 ? 'good' : 'bad'}>{money(position.pnl)} / {number(position.pnl_pct)}%</span>
                <span>{position.trailing_enabled ? `${position.trailing_percent}%` : 'Off'}</span>
              </div>
            )) : <div className="empty">No positions</div>}
          </div>
        </section>

        <section className="panel wide-panel">
          <PanelHeader icon={<RotateCcw size={16} />} title="Decision And Event Tape" />
          <div className="tape">
            {[...(snapshot?.decisions ?? []), ...(snapshot?.event_log ?? [])].slice(0, 18).map((item, index) => (
              <div className="tape-item" key={index}>
                <strong>{String(item.action ?? item.event_type ?? 'event')}</strong>
                <span>{String(item.symbol ?? item.session_id ?? '')}</span>
                <em>{String(item.reason ?? item.handoff_reason ?? item.status ?? '')}</em>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}

function Badge({ tone, label }: { tone: 'good' | 'warn' | 'bad' | 'neutral'; label: string }) {
  return <span className={`badge ${tone}`}>{label}</span>;
}

function PanelHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="panel-header">
      {icon}
      <h2>{title}</h2>
    </div>
  );
}

function Metric({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub: string }) {
  return (
    <div className="metric">
      <div>{icon}<span>{label}</span></div>
      <strong>{value}</strong>
      <p>{sub}</p>
    </div>
  );
}

function NumberField({ label, value, step = 1, onChange }: { label: string; value: number; step?: number; onChange: (value: number) => void }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" value={value} step={step} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}
