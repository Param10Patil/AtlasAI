import { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const SCENARIOS = [
  { value: 'deployment_failure', label: 'Deployment failure' },
  { value: 'pod_crash', label: 'Pod crash / restart' },
  { value: 'rollout_failure', label: 'Rollout readiness failure' },
];
const EXAMPLES = [
  { label: 'Payment outage', description: "Our payment API started returning 503 errors after today's deployment." },
  { label: 'Database pressure', description: 'Database connections are exhausted and checkout requests are timing out.' },
  { label: 'High latency', description: 'Our API p95 latency doubled after the latest release.' },
];
const stages = ['Detect', 'Diagnose', 'Evidence', 'Decision', 'Verify'];
const textValue = (value, fallback = '') => typeof value === 'string' ? value.trim() : fallback;
const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const clamp = (value) => Math.min(1, Math.max(0, finite(value)));
const requestId = () => window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;

function normalizeResult(payload) {
  const analysis = payload?.analysis;
  if (!analysis || typeof analysis !== 'object') throw new Error('The analysis response was incomplete.');
  const details = payload?.details && typeof payload.details === 'object' ? payload.details : {};
  const severityValue = textValue(analysis.severity, 'unknown').toLowerCase();
  return {
    requestId: textValue(payload.request_id, 'unavailable'),
    title: textValue(analysis.title, 'Incident analysis'),
    severity: ['low', 'medium', 'high', 'critical'].includes(severityValue) ? severityValue : 'unknown',
    causes: Array.isArray(analysis.likely_causes) ? analysis.likely_causes.filter((item) => typeof item === 'string' && item.trim()).slice(0, 5) : [],
    actions: Array.isArray(analysis.recommended_actions) ? analysis.recommended_actions.filter((item) => item && typeof item === 'object').slice(0, 5) : [],
    evidence: Array.isArray(analysis.evidence) ? analysis.evidence.filter((item) => item && typeof item === 'object').slice(0, 8) : [],
    confidence: clamp(analysis.confidence),
    limitations: Array.isArray(analysis.limitations) ? analysis.limitations.filter((item) => typeof item === 'string' && item.trim()).slice(0, 5) : [],
    details,
    knowledge: details.knowledge && typeof details.knowledge === 'object' ? details.knowledge : {},
    remediation: details.remediation && typeof details.remediation === 'object' ? details.remediation : {},
  };
}

async function readNdjson(response, signal, onEvent) {
  if (!response.body) throw new Error('Live progress is unavailable. Please try again.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result = null;
  const consume = (line) => {
    const clean = line.trim();
    if (!clean) return;
    let event;
    try { event = JSON.parse(clean); } catch { throw new Error('Live progress returned malformed data.'); }
    onEvent(event);
    if (event.status === 'complete' && event.result) result = event.result;
    if (event.status === 'failed') throw new Error(textValue(event.error?.message, 'The investigation failed. Try again.'));
    if (event.status === 'cancelled') throw new Error('The investigation was cancelled.');
  };
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || '';
    lines.forEach(consume);
  }
  consume(buffer + decoder.decode());
  return result;
}

function Dot({ state = 'neutral' }) { return <span className={`dot dot-${state}`} aria-hidden="true" />; }
function Badge({ children, tone = 'neutral' }) { return <span className={`badge badge-${tone}`}><Dot state={tone} />{children}</span>; }
function NumberMetric({ label, value, detail }) { return <div className="overview-metric"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>; }

function Header({ runtime }) {
  const cluster = runtime.cluster === 'connected' ? 'CONNECTED' : runtime.cluster === 'degraded' ? 'DEGRADED' : runtime.cluster === 'checking' ? 'CHECKING' : 'OFFLINE';
  const tone = runtime.cluster === 'connected' ? 'good' : runtime.cluster === 'checking' ? 'warn' : 'bad';
  return <header className="topbar"><div className="topbar-inner"><a className="brand" href="/" aria-label="OpsPilot home"><span className="brand-mark">OP</span><span><strong>OpsPilot</strong><small>Autonomous incident response</small></span></a><div className="topbar-status"><Badge tone={tone}>{cluster}</Badge><span className="status-divider" /><span className="ai-status"><Dot state={runtime.api === 'ready' ? 'good' : 'warn'} />AI {runtime.lora ? 'READY' : 'FALLBACK'}</span><span className="mode-label">{runtime.mode === 'execute' ? 'CONTROLLED CLUSTER' : 'CLOUD / LOCAL'}</span></div></div></header>;
}

function Overview({ runtime, cluster, observation, clusterError, onInspect }) {
  const clusterTone = runtime.cluster === 'connected' ? 'good' : runtime.cluster === 'checking' ? 'warn' : 'bad';
  const deployments = cluster ? `${cluster.deployments_available} / ${cluster.deployments_total}` : '—';
  const pods = cluster ? `${cluster.pods_ready} / ${cluster.pods_total}` : '—';
  const health = observation?.health?.status || (cluster ? 'connected' : 'offline');
  return <aside className="overview-panel" aria-label="Operations overview">
    <div className="overview-heading"><p className="eyebrow">OVERVIEW</p><span className="live-label"><Dot state={clusterTone} /> LIVE STATE</span></div>
    <section className="overview-section"><div className="overview-section-title"><h2>Cluster</h2><Badge tone={clusterTone}>{health}</Badge></div><p className="overview-copy">{runtime.cluster === 'connected' ? `${runtime.namespace} / ${runtime.workload}` : clusterError || 'Connect a Kubernetes cluster to inspect live state.'}</p><button className="quiet-button" type="button" onClick={onInspect}>Inspect current cluster <span aria-hidden="true">↗</span></button></section>
    <section className="overview-section"><p className="eyebrow">CAPACITY</p><div className="metric-grid"><NumberMetric label="Deployments" value={deployments} detail="available / desired" /><NumberMetric label="Pods" value={pods} detail="ready / observed" /></div></section>
    <section className="overview-section overview-note"><p className="eyebrow">CONTROL POSTURE</p><p>{runtime.mode === 'execute' ? 'Actions are namespace-scoped and require approval unless auto-remediation is enabled.' : 'Read-only until an approved Kubernetes executor is connected.'}</p></section>
    <div className="overview-footer"><span className="footer-key">MCP</span><span>Structured operational boundary</span></div>
  </aside>;
}

function CommandHeader({ onInspect, disabled }) {
  return <div className="command-heading"><div><p className="eyebrow">INCIDENT COMMAND CENTER</p><h1>Make the next decision obvious.</h1><p className="command-lede">Combine a human signal with live Kubernetes observations, then let evidence and policy shape the response.</p></div><button className="outline-button" type="button" onClick={onInspect} disabled={disabled} title="Read the approved demo workload from Kubernetes">Inspect cluster</button></div>;
}

function InvestigationComposer({ description, setDescription, validation, isBusy, onSubmit, onExample }) {
  const count = useMemo(() => `${description.length.toLocaleString()} / 4,000`, [description.length]);
  return <section className="command-card composer" aria-labelledby="incident-form-title"><div className="card-kicker"><span className="step-index">01</span><div><p className="eyebrow">NEW INVESTIGATION</p><h2 id="incident-form-title">What is happening?</h2></div><span className="card-hint">Human signal</span></div><form onSubmit={onSubmit} noValidate><label htmlFor="incident-description">Incident description</label><textarea id="incident-description" value={description} onChange={(event) => setDescription(event.target.value.slice(0, 4000))} maxLength="4000" aria-describedby="incident-hint incident-count incident-validation" aria-invalid={Boolean(validation)} placeholder="Service, symptom, and what changed..." /><div className="field-meta"><span id="incident-hint">Specific signals improve retrieval and diagnosis.</span><output id="incident-count">{count}</output></div><p id="incident-validation" className="validation-message" role="alert" hidden={!validation}>{validation}</p><div className="composer-footer"><span className="advisory"><Dot state="good" /> Advisory until the policy gate passes</span><button className="primary-button" type="submit" disabled={isBusy || !description.trim()}>{isBusy ? 'Investigating...' : 'Analyze investigation'} <span aria-hidden="true">→</span></button></div></form><div className="example-row"><span>Try a signal</span>{EXAMPLES.map((example) => <button type="button" key={example.label} onClick={() => onExample(example.description)} disabled={isBusy} title={`Use ${example.label.toLowerCase()} example`}>{example.label}</button>)}</div></section>;
}

function SimulationPanel({ enabled, busy, scenario, setScenario, auto, setAuto, onInject }) {
  return <section className="command-card simulation-card" aria-labelledby="simulation-title"><div className="simulation-copy"><p className="eyebrow">CONTROLLED DEMO ENVIRONMENT</p><h2 id="simulation-title">Inject a reversible incident</h2><p>Changes only the allowlisted <code>ops-demo/checkout-api</code> workload. OpsPilot then observes the cluster, investigates, and verifies recovery.</p></div><div className="simulation-controls"><label htmlFor="scenario">Failure type</label><select id="scenario" value={scenario} onChange={(event) => setScenario(event.target.value)} disabled={!enabled || busy}>{SCENARIOS.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select><label className="toggle-row"><input type="checkbox" checked={auto} onChange={(event) => setAuto(event.target.checked)} disabled={!enabled || busy} /><span>Auto-remediate after policy approval</span></label><button className="outline-button" type="button" onClick={onInject} disabled={!enabled || busy}>{busy ? 'Working...' : 'Inject controlled incident'} <span aria-hidden="true">↗</span></button></div>{!enabled && <p className="simulation-warning"><Dot state="warn" /> Connect an executable Kubernetes demo cluster to enable injection.</p>}</section>;
}

function Timeline({ phase, result }) {
  const complete = result?.details?.steps || [];
  const active = phase === 'starting' || phase === 'queued' || phase === 'running';
  return <section className="timeline" aria-label="Investigation timeline"><div className="timeline-head"><p className="eyebrow">WORKFLOW</p><span>{active ? 'LIVE' : result ? 'COMPLETE' : 'READY'}</span></div><div className="timeline-rail">{stages.map((stage, index) => { const item = complete[index]; const done = item?.status === 'complete' || (result && index < 4); const current = active && index === (phase === 'queued' ? 0 : 1); return <div className={`timeline-item ${done ? 'is-done' : current ? 'is-current' : ''}`} key={stage}><span className="timeline-node"><Dot state={done ? 'good' : current ? 'warn' : 'neutral'} /></span><strong>{stage}</strong><small>{item?.status || (current ? 'in progress' : 'pending')}</small></div>; })}</div></section>;
}

function Observation({ title, value }) {
  const deployment = value?.deployment;
  const pods = Array.isArray(value?.pods) ? value.pods : [];
  if (!value) return <div className="empty-observation"><Dot state="neutral" /> No Kubernetes observation attached.</div>;
  return <div className="observation-block"><div className="observation-head"><div><p className="eyebrow">{title}</p><strong>{value.namespace} / {value.workload}</strong></div><Badge tone={value.health?.status === 'healthy' ? 'good' : value.health?.status === 'degraded' ? 'bad' : 'warn'}>{value.health?.status || 'unknown'}</Badge></div><div className="observation-grid">{deployment && <NumberMetric label="Ready replicas" value={`${deployment.ready_replicas} / ${deployment.desired_replicas}`} detail={deployment.rollout_complete ? 'rollout complete' : 'rollout incomplete'} />}<NumberMetric label="Observed pods" value={pods.length} detail="from Kubernetes" /><NumberMetric label="Restarts" value={pods.reduce((sum, pod) => sum + finite(pod.restarts), 0)} detail="reported by pods" /></div>{value.health?.reasons?.length > 0 && <ul className="signal-list">{value.health.reasons.slice(0, 5).map((reason) => <li key={reason}>{reason}</li>)}</ul>}{value.events?.length > 0 && <details className="events-disclosure"><summary>Recent Kubernetes events ({value.events.length})</summary><ul className="event-list">{value.events.slice(-8).map((event) => <li key={event.name}><strong>{event.reason || 'Event'}</strong><span>{event.message}</span></li>)}</ul></details>}</div>;
}

function RemediationCard({ result, job, onApprove }) {
  const remediation = result.remediation || {};
  const before = remediation.before_observation;
  const after = remediation.after_observation;
  const verified = remediation.status === 'verified' && remediation.health_verified;
  const waiting = remediation.status === 'awaiting_approval';
  const actionName = textValue(remediation.action, result.actions[0]?.text || 'Review evidence');
  return <section className={`remediation-card ${verified ? 'is-resolved' : waiting ? 'is-waiting' : ''}`}><div className="remediation-head"><div><p className="eyebrow">{verified ? 'INCIDENT RESOLVED' : 'RECOMMENDED REMEDIATION'}</p><h2>{verified ? 'Recovery verified by Kubernetes' : actionName.replaceAll('_', ' ')}</h2></div><Badge tone={verified ? 'good' : waiting ? 'warn' : remediation.status === 'failed' ? 'bad' : 'neutral'}>{verified ? 'RESOLVED' : remediation.status || 'REVIEW'}</Badge></div><p className="remediation-message">{textValue(remediation.message, 'Review the ranked plan and confirm the safest next step.')}</p>{before || after ? <div className="before-after"><Observation title="Before" value={before} /><span className="transition-arrow" aria-hidden="true">↓</span><Observation title="After" value={after} /></div> : <div className="remediation-scope"><span>Scope</span><strong>{textValue(remediation.target, 'Approved workload only')}</strong><span>Safety</span><strong>Allowlist + evidence gate</strong></div>}{waiting && job?.job_id && <button className="primary-button" type="button" onClick={onApprove}>Approve & remediate <span aria-hidden="true">→</span></button>}{verified && <p className="verified-note"><Dot state="good" /> Health verification passed from the cluster observation.</p>}</section>;
}

function ResultView({ result, job, onApprove, onNew, onCopy, copyState }) {
  const details = result.details || {};
  const limitation = result.limitations.length ? result.limitations.join(' · ') : 'No additional limitation reported.';
  const knowledge = result.knowledge || {};
  return <section className="results" aria-labelledby="result-title"><div className="result-title-row"><div><p className="eyebrow">INCIDENT REVIEW · {result.requestId.slice(0, 12)}</p><h2 id="result-title" tabIndex="-1">{result.title}</h2></div><Badge tone={result.severity === 'high' || result.severity === 'critical' ? 'bad' : result.severity === 'medium' ? 'warn' : 'good'}>{result.severity}</Badge></div><div className="diagnosis-grid"><section className="command-card diagnosis-card"><div className="diagnosis-lead"><div><p className="eyebrow">PROBABLE ROOT CAUSE</p><p className="cause-lead">{result.causes[0] || 'No likely cause was isolated.'}</p></div><div className="confidence"><span>Confidence</span><strong>{Math.round(result.confidence * 100)}%</strong><div className="confidence-bar"><span style={{ width: `${Math.round(result.confidence * 100)}%` }} /></div></div></div><div className="diagnosis-columns"><div><p className="eyebrow">OBSERVED SIGNALS</p><ul className="signal-list">{(result.causes.length ? result.causes : ['No additional signal was reported.']).map((cause) => <li key={cause}>{cause}</li>)}</ul></div><div><p className="eyebrow">RANKED PLAN</p><ol className="plan-list">{(result.actions.length ? result.actions : [{ text: 'Review evidence with an operator.' }]).map((action, index) => <li key={`${action.text}-${index}`}><span className="plan-rank">{action.rank || index + 1}</span><div><strong>{textValue(action.text, 'Review with an operator.')}</strong>{action.evidence_ids?.length > 0 && <small>Evidence: {action.evidence_ids.join(', ')}</small>}</div></li>)}</ol></div></div></section><RemediationCard result={result} job={job} onApprove={onApprove} /></div><div className="evidence-layout"><section className="command-card evidence-card"><div className="card-kicker"><span className="step-index">02</span><div><p className="eyebrow">EVIDENCE</p><h2>Why this result is grounded</h2></div><span className="card-hint">{result.evidence.length} sources</span></div><div className="retrieval-proof"><strong>{textValue(knowledge.retrieval_method, 'retrieval unavailable').replaceAll('_', ' ')}</strong><span>Top match {Math.round(clamp(knowledge.best_score) * 100)}% · {finite(knowledge.candidate_count)} candidates retained</span></div><ul className="evidence-list">{result.evidence.length ? result.evidence.map((item, index) => <li key={`${item.id || item.title}-${index}`}><div><span className="evidence-kind">{textValue(item.kind, 'RUNBOOK')}</span><strong>{textValue(item.title, 'Source')}</strong></div><span className="evidence-score">{Math.round(clamp(item.score) * 100)}%</span><p>{textValue(item.excerpt, 'No excerpt supplied.')}</p></li>) : <li className="empty-state">No sufficiently relevant operational evidence found.</li>}</ul></section><section className="command-card observation-card"><div className="card-kicker"><span className="step-index">03</span><div><p className="eyebrow">KUBERNETES OBSERVATION</p><h2>Infrastructure facts</h2></div></div><Observation title="Observed state" value={details.observation} /></section></div><details className="command-card technical-details"><summary><span><span className="eyebrow">TECHNICAL DETAILS</span><strong>Agent trace, MCP calls, and safety decisions</strong></span><span aria-hidden="true">＋</span></summary><div className="technical-grid"><div><p className="eyebrow">LANGGRAPH TRACE</p><ul className="technical-list">{(details.steps || []).map((step) => <li key={step.name}><Dot state={step.status === 'complete' ? 'good' : 'warn'} /><span>{step.name}</span><strong>{step.status}</strong></li>)}</ul></div><div><p className="eyebrow">MCP TOOLS</p><ul className="technical-list">{(details.tools || []).length ? details.tools.map((tool) => <li key={tool.name}><Dot state={tool.status === 'complete' ? 'good' : 'warn'} /><span>{tool.name}</span><strong>{tool.status}</strong></li>) : <li><Dot /><span>No operational tools called</span></li>}</ul></div><div><p className="eyebrow">CONTEXT BOUNDARIES</p><ul className="context-list"><li>TriageContext <span>incident + observation</span></li><li>KnowledgeContext <span>triage + focused signals</span></li><li>ResolutionContext <span>triage + selected evidence</span></li><li>RemediationContext <span>policy-approved action</span></li></ul></div><div><p className="eyebrow">LIMITATIONS</p><p className="technical-copy">{limitation}</p><p className="technical-copy">Classifier: {textValue(details.models?.classifier, 'unknown')} · Resolution: {textValue(details.models?.resolution, 'unknown')}</p></div></div></details><div className="result-actions"><button className="outline-button" type="button" onClick={onCopy}>{copyState || 'Copy summary'}</button><button className="quiet-button" type="button" onClick={onNew}>Start new investigation</button></div></section>;
}

function ProgressCard({ phase, job, onCancel }) {
  const queued = phase === 'queued';
  const copy = queued ? 'Waiting for the bounded analysis slot.' : phase === 'starting' ? 'Connecting to the analysis service.' : 'Reading the incident, cluster signals, and supporting evidence.';
  return <section className="command-card progress-card" aria-live="polite" aria-busy="true"><div className="spinner" aria-hidden="true" /><div><p className="eyebrow">{queued ? 'QUEUED' : 'INVESTIGATING'}</p><h2>{copy}</h2><p className="progress-copy">{job?.position > 0 ? `Queue position ${job.position}.` : 'The investigation remains read-only until the safety gate is evaluated.'}</p><button className="quiet-button" type="button" onClick={onCancel}>Cancel request</button></div></section>;
}

function App() {
  const [description, setDescription] = useState('');
  const [validation, setValidation] = useState('');
  const [phase, setPhase] = useState('idle');
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [copyState, setCopyState] = useState('');
  const [runtime, setRuntime] = useState({ api: 'checking', cluster: 'checking', lora: false, mode: 'disabled', namespace: 'ops-demo', workload: 'checkout-api' });
  const [cluster, setCluster] = useState(null);
  const [observation, setObservation] = useState(null);
  const [clusterError, setClusterError] = useState('');
  const [scenario, setScenario] = useState(SCENARIOS[0].value);
  const [autoRemediate, setAutoRemediate] = useState(false);
  const activeRef = useRef(null);
  const controllerRef = useRef(null);
  const timeoutRef = useRef(null);
  const cancelledRef = useRef(false);
  const lastDescriptionRef = useRef('');
  const isBusy = ['starting', 'queued', 'running'].includes(phase);

  const loadCluster = async () => {
    try {
      const response = await fetch('/api/cluster/summary');
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.message || 'Cluster summary unavailable.');
      setCluster(payload); setClusterError('');
    } catch (caught) { setCluster(null); setClusterError(textValue(caught.message, 'Cluster connection unavailable.')); }
  };
  const inspectCluster = async () => {
    try {
      const response = await fetch('/api/cluster/observations');
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.message || 'Cluster observations unavailable.');
      setObservation(payload); setClusterError(''); await loadCluster();
    } catch (caught) { setClusterError(textValue(caught.message, 'Cluster observations unavailable.')); }
  };
  useEffect(() => {
    let active = true;
    fetch('/api/ready').then(async (response) => ({ ok: response.ok, payload: await response.json().catch(() => ({})) })).then(({ ok, payload }) => {
      if (!active) return;
      const optional = payload.optional || {};
      setRuntime({ api: ok && payload.status === 'ready' ? 'ready' : 'offline', cluster: optional.kubernetes || 'offline', lora: optional.lora === 'available', mode: optional.kubernetes_mode || 'disabled', namespace: 'ops-demo', workload: 'checkout-api' });
      if (optional.kubernetes === 'connected') loadCluster();
    }).catch(() => { if (active) setRuntime((current) => ({ ...current, api: 'offline', cluster: 'offline' })); });
    return () => { active = false; controllerRef.current?.abort(); window.clearTimeout(timeoutRef.current); };
  }, []);

  const handleEvent = (event, id) => {
    if (activeRef.current !== id) return;
    if (event.status === 'queued') { setPhase('queued'); setJob((current) => ({ ...current, position: event.position })); }
    if (event.status === 'running') setPhase('running');
    if (event.status === 'complete') setPhase('success');
  };
  const statusOnce = async (url, signal, id) => {
    const response = await fetch(url, { signal });
    const payload = await response.json().catch(() => ({}));
    if (activeRef.current !== id) return null;
    if (!response.ok) throw new Error('Investigation status unavailable.');
    return payload;
  };
  const consumeJob = async (created, signal, id) => {
    let attempts = 0;
    while (activeRef.current === id) {
      try {
        const response = await fetch(created.events_url, { signal });
        if (!response.ok) throw new Error('stream');
        const streamed = await readNdjson(response, signal, (event) => handleEvent(event, id));
        if (streamed) return streamed;
        const status = await statusOnce(created.status_url, signal, id);
        if (status?.result) return status.result;
        if (['complete', 'failed', 'cancelled'].includes(status?.status)) return null;
        throw new Error('stream-ended');
      } catch (streamError) {
        if (streamError.name === 'AbortError' || cancelledRef.current || activeRef.current !== id) throw streamError;
        if (attempts >= 2) throw new Error('Live progress was interrupted. Try again.');
        attempts += 1;
        const status = await statusOnce(created.status_url, signal, id);
        if (status?.result) return status.result;
        if (['failed', 'cancelled'].includes(status?.status)) return null;
        await new Promise((resolve) => window.setTimeout(resolve, 250 * (2 ** (attempts - 1))));
      }
    }
    return null;
  };
  const executeJob = async (created, value) => {
    const id = requestId();
    const controller = new AbortController();
    activeRef.current = id; controllerRef.current = controller; cancelledRef.current = false; lastDescriptionRef.current = value;
    setPhase(created.status === 'queued' ? 'queued' : 'starting'); setJob(created); setResult(null); setError(null); setValidation('');
    timeoutRef.current = window.setTimeout(() => controller.abort(), 125000);
    try {
      const rawResult = await consumeJob(created, controller.signal, id);
      if (rawResult) { setResult(normalizeResult(rawResult)); setPhase('success'); }
      else if (!cancelledRef.current) throw new Error('The analysis service returned no result.');
    } catch (caught) {
      if (caught.name === 'AbortError' && !cancelledRef.current) setError({ message: 'The investigation took too long. Try again.', retryable: true });
      else if (activeRef.current === id) setError({ message: cancelledRef.current ? 'The investigation was cancelled.' : textValue(caught.message, 'Investigation failed. Try again.'), retryable: !cancelledRef.current });
      setPhase('error');
    } finally {
      if (activeRef.current === id) { window.clearTimeout(timeoutRef.current); timeoutRef.current = null; activeRef.current = null; controllerRef.current = null; }
    }
  };
  const runRequest = async (url, body, value) => {
    if (isBusy) return;
    setPhase('starting'); setError(null); setResult(null);
    try {
      const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(textValue(payload.error?.message, payload.message || 'The request could not be started.'));
      if (!payload.job_id) { if (payload.observation) setObservation(payload.observation); throw new Error(payload.message || 'No investigation job was created.'); }
      await executeJob(payload, value);
    } catch (caught) { setError({ message: textValue(caught.message, 'The request could not be started.'), retryable: true }); setPhase('error'); }
  };
  const run = (value) => runRequest('/api/incidents/analyze/jobs', { description: value, client_request_id: requestId() }, value);
  const submit = (event) => { event.preventDefault(); const value = description.trim(); if (isBusy) return; if (!value) { setValidation('Describe the incident before starting an investigation.'); return; } run(value); };
  const inject = () => runRequest('/api/cluster/simulations/inject', { scenario, auto_remediate: autoRemediate }, `Controlled ${scenario} in ops-demo`);
  const approve = async () => {
    if (!job?.job_id || isBusy) return;
    try {
      const response = await fetch(`/api/incidents/analyze/jobs/${encodeURIComponent(job.job_id)}/approve`, { method: 'POST' });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(textValue(payload.error?.message, 'Approval could not be started.'));
      await executeJob(payload, lastDescriptionRef.current);
    } catch (caught) { setError({ message: textValue(caught.message, 'Approval could not be started.'), retryable: true }); setPhase('error'); }
  };
  const cancel = async () => { if (!job?.job_id || !activeRef.current) return; cancelledRef.current = true; try { await fetch(`/api/incidents/analyze/jobs/${encodeURIComponent(job.job_id)}`, { method: 'DELETE' }); } catch { /* local cancellation still applies */ } controllerRef.current?.abort(); };
  const retry = () => { if (lastDescriptionRef.current) run(lastDescriptionRef.current); };
  const reset = () => { if (isBusy) return; setDescription(''); setValidation(''); setPhase('idle'); setJob(null); setResult(null); setError(null); setCopyState(''); };
  const copySummary = async () => { if (!result) return; const summary = `${result.title}\nSeverity: ${result.severity}\nCause: ${result.causes[0] || 'Not isolated'}\nRecommendation: ${textValue(result.actions[0]?.text, 'Review with an operator.')}`; try { await navigator.clipboard.writeText(summary); setCopyState('Copied'); window.setTimeout(() => setCopyState(''), 1800); } catch { setCopyState('Copy unavailable'); } };
  const simulationEnabled = runtime.cluster === 'connected' && runtime.mode === 'execute';

  return <><Header runtime={runtime} /><main className="app-shell"><Overview runtime={runtime} cluster={cluster} observation={observation || result?.details?.observation} clusterError={clusterError} onInspect={inspectCluster} /><section className="command-column"><CommandHeader onInspect={inspectCluster} disabled={runtime.cluster !== 'connected'} /><InvestigationComposer description={description} setDescription={(value) => { setDescription(value); setValidation(''); }} validation={validation} isBusy={isBusy} onSubmit={submit} onExample={setDescription} /><SimulationPanel enabled={simulationEnabled} busy={isBusy} scenario={scenario} setScenario={setScenario} auto={autoRemediate} setAuto={setAutoRemediate} onInject={inject} />{(isBusy || result || error) && <Timeline phase={phase} result={result} />}{isBusy && <ProgressCard phase={phase} job={job} onCancel={cancel} />}{error && <section className="command-card error-card" role="alert"><p className="eyebrow">INVESTIGATION PAUSED</p><h2>{error.message}</h2><div className="error-actions">{error.retryable && <button className="outline-button" type="button" onClick={retry}>Try again</button>}<button className="quiet-button" type="button" onClick={reset}>Start new investigation</button></div></section>}{result && phase === 'success' && <ResultView result={result} job={job} onApprove={approve} onNew={reset} onCopy={copySummary} copyState={copyState} />}</section></main><footer className="app-footer"><span>OpsPilot</span><span>Policy-gated infrastructure assistance</span></footer></>;
}

createRoot(document.getElementById('root')).render(<App />);
