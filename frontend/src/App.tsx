import React from 'react';
import {
  Activity,
  ArrowRightLeft,
  Banknote,
  Database,
  Download,
  FileUp,
  MessageSquare,
  Pause,
  Play,
  PlugZap,
  RadioTower,
  RotateCcw,
  Save,
  ShieldCheck,
  SkipForward,
  SlidersHorizontal,
  Upload,
} from 'lucide-react';
import {
  api,
  type SentinelEchoReplayResponse,
  type SentinelEchoTestRun,
  type DiscordTestResult,
  type ExportRecord,
  type ParsedAlert,
  type PriceDriftEvent,
  type RecorderSettings,
  type RecorderStatus,
  type SimulationConfig,
  type SimulationSnapshot,
} from './api';

const emptyCsv = 'timestamp,symbol,open,high,low,close,volume\n';
const emptyDiscordCsv = 'message_id,channel_id,channel_name,author_id,author_name,discord_timestamp,content\n';
const emptyOptionCsv = 'timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume,bid,ask,last\n';

function money(value: unknown) {
  if (value === null || value === undefined || value === '') return 'Unavailable';
  const number = Number(value);
  if (!Number.isFinite(number)) return 'Unavailable';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(number);
}

function number(value: unknown, digits = 2) {
  if (value === null || value === undefined || value === '') return 'Unavailable';
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 'Unavailable';
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(parsed);
}

function isoMinute() {
  return Math.floor(Date.now() / 60000);
}

function parseChannelIds(value: string) {
  const ids: string[] = [];
  for (const part of value.split(/[\s,;]+/)) {
    const clean = part.trim();
    if (clean && !ids.includes(clean)) ids.push(clean);
  }
  return ids;
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
  const [recorderSettings, setRecorderSettings] = React.useState<RecorderSettings | null>(null);
  const [recorderDirty, setRecorderDirty] = React.useState(false);
  const [recorderStatus, setRecorderStatus] = React.useState<RecorderStatus | null>(null);
  const [discordTestResult, setDiscordTestResult] = React.useState<DiscordTestResult | null>(null);
  const [previewText, setPreviewText] = React.useState('BTO SPY 500C 6/21 @ 1.25');
  const [previewAlert, setPreviewAlert] = React.useState<ParsedAlert | null>(null);
  const [discordCsvText, setDiscordCsvText] = React.useState(emptyDiscordCsv);
  const [optionsCsvText, setOptionsCsvText] = React.useState(emptyOptionCsv);
  const [stocksCsvText, setStocksCsvText] = React.useState(emptyCsv);
  const [recorderAlerts, setRecorderAlerts] = React.useState<ParsedAlert[]>([]);
  const [driftEvents, setDriftEvents] = React.useState<PriceDriftEvent[]>([]);
  const [exportChannelIdsText, setExportChannelIdsText] = React.useState('');
  const [exportType, setExportType] = React.useState<'alerts' | 'joined'>('joined');
  const [exports, setExports] = React.useState<ExportRecord[]>([]);
  const [sentinelEchoChannelIdsText, setSentinelEchoChannelIdsText] = React.useState('');
  const [sentinelEchoSince, setSentinelEchoSince] = React.useState('');
  const [sentinelEchoReplay, setSentinelEchoReplay] = React.useState<SentinelEchoReplayResponse | null>(null);
  const [sentinelEchoTestRun, setSentinelEchoTestRun] = React.useState<SentinelEchoTestRun | null>(null);

  const refresh = React.useCallback(async () => {
    const [next, settings, recorder, alerts, drift, exportList] = await Promise.all([
      api.state(),
      api.recorderSettings(),
      api.recorderStatus(),
      api.recorderAlerts(),
      api.recorderDriftEvents(),
      api.recorderExports(),
    ]);
    setSnapshot(next);
    setRecorderSettings((current) => (recorderDirty && current ? current : settings));
    setRecorderStatus(recorder);
    setRecorderAlerts(alerts.alerts);
    setDriftEvents(drift.drift_events);
    setExports(exportList.exports);
    setConfigDraft((current) => current ?? next.config);
    if (!selectedSession && next.sessions[0]) setSelectedSession(next.sessions[0].session_id);
  }, [recorderDirty, selectedSession]);

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

  function updateRecorder<K extends keyof RecorderSettings>(key: K, value: RecorderSettings[K]) {
    setRecorderDirty(true);
    setRecorderSettings((current) => (current ? { ...current, [key]: value } : current));
  }

  async function saveRecorderSettings(settings: RecorderSettings) {
    const saved = await api.updateRecorderSettings(settings);
    setRecorderSettings(saved);
    setRecorderDirty(false);
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
  const driftByAlert = React.useMemo(() => new Map(driftEvents.map((event) => [event.alert_id, event])), [driftEvents]);
  const latestExport = exports[0];
  const exportChannelIds = React.useMemo(() => parseChannelIds(exportChannelIdsText), [exportChannelIdsText]);
  const sentinelEchoChannelIds = React.useMemo(() => parseChannelIds(sentinelEchoChannelIdsText), [sentinelEchoChannelIdsText]);
  const sentinelEchoReplayUrl = React.useMemo(() => {
    const params = new URLSearchParams();
    if (sentinelEchoChannelIds.length) params.set('channel_ids', sentinelEchoChannelIds.join(','));
    if (sentinelEchoSince.trim()) params.set('since', sentinelEchoSince.trim());
    params.set('limit', '100');
    return `/api/sentinel-echo/replay/events?${params.toString()}`;
  }, [sentinelEchoChannelIds, sentinelEchoSince]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span>Sentinel</span>
          <strong>Sentinel Archive</strong>
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

        <section className="panel tall">
          <PanelHeader icon={<MessageSquare size={16} />} title="Discord Recorder" />
          {recorderSettings ? (
            <div className="stack">
              <div className="recorder-status">
                <Badge tone={recorderStatus?.discord_connected ? 'good' : recorderStatus?.discord_state === 'failed' ? 'bad' : 'neutral'} label={recorderStatus?.discord_state || 'stopped'} />
                <span>{number(recorderStatus?.messages_recorded, 0)} messages</span>
                <span>{number(recorderStatus?.parsed_alerts, 0)} parsed</span>
                <span>{number(recorderStatus?.drift_alerts, 0)} drift flags</span>
                <span>{recorderStatus?.active_session_id ? `session ${recorderStatus.active_session_id}` : 'no session'}</span>
              </div>
              <label className="field">
                <span>Bot token</span>
                <input type="password" value={recorderSettings.discord_token} onChange={(event) => updateRecorder('discord_token', event.target.value)} />
              </label>
              <label className="field">
                <span>Channel IDs</span>
                <textarea
                  className="compact-textarea"
                  value={recorderSettings.discord_channel_ids.join('\n')}
                  onChange={(event) => updateRecorder('discord_channel_ids', parseChannelIds(event.target.value))}
                  spellCheck={false}
                />
              </label>
              <ChannelChips ids={recorderSettings.record_all_channels ? ['*'] : recorderSettings.discord_channel_ids} emptyLabel="No channel IDs configured" />
              <div className="form-grid compact">
                <NumberField label="Drift $" value={recorderSettings.drift_amount_threshold} step={0.01} onChange={(value) => updateRecorder('drift_amount_threshold', value)} />
                <NumberField label="Drift %" value={recorderSettings.drift_percent_threshold} onChange={(value) => updateRecorder('drift_percent_threshold', value)} />
                <label className="check">
                  <input type="checkbox" checked={recorderSettings.record_all_channels} onChange={(event) => updateRecorder('record_all_channels', event.target.checked)} />
                  <span>All channels</span>
                </label>
                <label className="check">
                  <input type="checkbox" checked={recorderSettings.yfinance_enabled} onChange={(event) => updateRecorder('yfinance_enabled', event.target.checked)} />
                  <span>Live quotes</span>
                </label>
              </div>
              <div className="button-row">
                <button className="primary" type="button" onClick={() => run('Saving recorder', () => saveRecorderSettings(recorderSettings))}>
                  <Save size={15} />
                  Save
                </button>
                <button type="button" onClick={() => run('Testing recorder', async () => setDiscordTestResult(await api.testDiscordRecorder()))}>
                  <PlugZap size={15} />
                  Test
                </button>
                <button type="button" onClick={() => run('Starting recorder', api.startDiscordRecorder)}>
                  <Play size={15} />
                  Start
                </button>
                <button type="button" onClick={() => run('Stopping recorder', api.stopDiscordRecorder)}>
                  <Pause size={15} />
                  Stop
                </button>
                <button type="button" onClick={() => run('Starting capture session', () => api.startRecordingSession('UI capture session'))}>
                  <Play size={15} />
                  Capture
                </button>
                <button type="button" onClick={() => run('Stopping capture session', api.stopRecordingSession)}>
                  <Pause size={15} />
                  End
                </button>
              </div>
              {discordTestResult ? <pre className="json-preview short">{JSON.stringify(discordTestResult, null, 2)}</pre> : null}
              <label className="field">
                <span>Parse preview</span>
                <textarea className="compact-textarea" value={previewText} onChange={(event) => setPreviewText(event.target.value)} spellCheck={false} />
              </label>
              <button type="button" onClick={() => run('Previewing parser', async () => setPreviewAlert(await api.parsePreview(previewText)))}>
                <MessageSquare size={15} />
                Preview
              </button>
              <pre className="json-preview">{previewAlert ? JSON.stringify(previewAlert, null, 2) : 'No preview yet'}</pre>
            </div>
          ) : null}
        </section>

        <section className="panel tall">
          <PanelHeader icon={<Database size={16} />} title="Recorder Imports" />
          <div className="stack">
            <label className="field">
              <span>Discord alert CSV</span>
              <textarea className="compact-textarea" value={discordCsvText} onChange={(event) => setDiscordCsvText(event.target.value)} spellCheck={false} />
            </label>
            <button type="button" onClick={() => run('Importing Discord CSV', () => api.importDiscordCsv(discordCsvText))}>
              <Upload size={15} />
              Import Alerts
            </button>
            <label className="field">
              <span>Option price CSV</span>
              <textarea className="compact-textarea" value={optionsCsvText} onChange={(event) => setOptionsCsvText(event.target.value)} spellCheck={false} />
            </label>
            <button type="button" onClick={() => run('Importing option prices', () => api.importOptionsCsv(optionsCsvText))}>
              <Database size={15} />
              Import Options
            </button>
            <label className="field">
              <span>Stock price CSV</span>
              <textarea className="compact-textarea" value={stocksCsvText} onChange={(event) => setStocksCsvText(event.target.value)} spellCheck={false} />
            </label>
            <button type="button" onClick={() => run('Importing stock prices', () => api.importStocksCsv(stocksCsvText))}>
              <Database size={15} />
              Import Stocks
            </button>
            <div className="export-row">
              <label className="field">
                <span>Export channels</span>
                <textarea className="compact-textarea channel-filter" value={exportChannelIdsText} onChange={(event) => setExportChannelIdsText(event.target.value)} placeholder="Blank exports all channels" spellCheck={false} />
              </label>
              <label className="field">
                <span>Export type</span>
                <select value={exportType} onChange={(event) => setExportType(event.target.value as 'alerts' | 'joined')}>
                  <option value="joined">joined</option>
                  <option value="alerts">alerts</option>
                </select>
              </label>
              <button className="primary" type="button" onClick={() => run('Exporting alerts', () => api.exportRecordings(exportChannelIds, exportType))}>
                <Download size={15} />
                Export
              </button>
            </div>
            <ChannelChips ids={exportChannelIds} emptyLabel="Export scope: all channels" />
            <p className="path-readout">{latestExport ? latestExport.file_path : 'No exports yet'}</p>
          </div>
        </section>

        <section className="panel tall">
          <PanelHeader icon={<PlugZap size={16} />} title="Sentinel Echo Replay" />
          <div className="stack">
            <label className="field">
              <span>Replay channels</span>
              <textarea className="compact-textarea channel-filter" value={sentinelEchoChannelIdsText} onChange={(event) => setSentinelEchoChannelIdsText(event.target.value)} placeholder="Blank replays all channels" spellCheck={false} />
            </label>
            <ChannelChips ids={sentinelEchoChannelIds} emptyLabel="Replay scope: all channels" />
            <label className="field">
              <span>Since</span>
              <input value={sentinelEchoSince} onChange={(event) => setSentinelEchoSince(event.target.value)} placeholder="2026-06-19T14:30:00+00:00" />
            </label>
            <div className="button-row">
              <button type="button" onClick={() => run('Loading Sentinel Echo replay', async () => setSentinelEchoReplay(await api.sentinelEchoReplayEvents(sentinelEchoChannelIds, sentinelEchoSince, 100)))}>
                <RotateCcw size={15} />
                Events
              </button>
              <button className="primary" type="button" onClick={() => run('Writing Sentinel Echo test run', async () => setSentinelEchoTestRun(await api.createSentinelEchoTestRun('Sentinel Echo UI test', sentinelEchoChannelIds, sentinelEchoSince, 1000)))}>
                <Download size={15} />
                JSONL
              </button>
            </div>
            <div className="recorder-status">
              <Badge tone={sentinelEchoReplay?.event_count ? 'good' : 'neutral'} label={`${sentinelEchoReplay ? number(sentinelEchoReplay.event_count, 0) : '0'} events`} />
              <span>{sentinelEchoReplay?.contract_version || 'simulation.sentinel-echo.replay.v1'}</span>
            </div>
            <p className="path-readout">{sentinelEchoReplayUrl}</p>
            <p className="path-readout">{sentinelEchoTestRun ? sentinelEchoTestRun.file_path : 'No test run yet'}</p>
            <pre className="json-preview">
              {sentinelEchoReplay?.events[0] ? JSON.stringify(sentinelEchoReplay.events[0], null, 2) : 'No replay event loaded'}
            </pre>
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
          <PanelHeader icon={<MessageSquare size={16} />} title="Recorded Alerts" />
          <div className="alert-table">
            <div className="alert-row head">
              <span>Status</span><span>Action</span><span>Contract</span><span>Alert</span><span>Market</span><span>Drift</span>
            </div>
            {recorderAlerts.length ? recorderAlerts.slice(0, 12).map((alert) => {
              const drift = driftByAlert.get(alert.message_id);
              const contract = [alert.ticker, alert.expiration, alert.strike, alert.option_type].filter(Boolean).join(' ');
              return (
                <div className="alert-row" key={alert.message_id}>
                  <span className={alert.parse_status === 'parsed' ? 'good' : 'bad'}>{alert.parse_status}</span>
                  <span>{alert.action || 'capture'}</span>
                  <span>{contract || 'Unparsed'}</span>
                  <span>{money(alert.alert_price)}</span>
                  <span>{money(drift?.market_price)}</span>
                  <span className={drift?.price_drift_alert ? 'bad' : 'good'}>{drift ? `${number(drift.price_drift_amount)} / ${number(drift.price_drift_pct)}%` : 'Unavailable'}</span>
                </div>
              );
            }) : <div className="empty">No recorded alerts</div>}
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

function ChannelChips({ ids, emptyLabel }: { ids: string[]; emptyLabel: string }) {
  const clean = ids.map((item) => item.trim()).filter(Boolean);
  return (
    <div className="channel-chip-row">
      {clean.length ? clean.map((id) => <span className="channel-chip" key={id}>{id === '*' ? 'all channels' : id}</span>) : <span className="channel-empty">{emptyLabel}</span>}
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
