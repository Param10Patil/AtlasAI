import { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

// Vercel serves the static console separately from the API. Keep local
// development same-origin by default, while allowing a deployed frontend to
// point at the Cloud Run origin through VITE_API_BASE_URL.
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '');
const apiUrl = (path) => /^https?:\/\//i.test(path) ? path : API_BASE_URL + path;
const apiFetch = (path, options) => fetch(apiUrl(path), options);

const SCENARIOS = [
  { value: 'deployment_failure', label: 'Deployment failure', impact: 'Temporarily points the demo deployment at a known-bad image.' },
  { value: 'pod_crash', label: 'Pod crash / restart', impact: 'Deletes one demo pod so its controller recreates it.' },
  { value: 'rollout_failure', label: 'Rollout readiness failure', impact: 'Applies a controlled readiness failure to the demo workload.' },
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
const requestId = () => window.crypto?.randomUUID?.() || (Date.now() + '-' + Math.random());
const humanize = (value, fallback = 'Unknown') => {
  const text = textValue(value, fallback).replaceAll('_', ' ').replace(/\s+/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
};
const resourceName = (value) => textValue(value, 'workload').replace(/^ops-demo\//, '');
const healthTone = (value) => value === 'healthy' ? 'good' : value === 'degraded' ? 'bad' : 'warn';
const toolPurpose = (name) => ({
  get_service_observations: 'Read the selected workload state from the Kubernetes observation boundary.',
  search_runbooks: 'Retrieve the most relevant operational guidance from the local vector index.',
  get_incident_history: 'Look up bounded historical incidents for the triage category.',
  execute_safe_action: 'Call one of the four policy-allowlisted remediation operations.',
  verify_health: 'Take a fresh Kubernetes observation after an operation.',
}[name] || 'Structured operational tool call.');

function normalizeResult(payload) {
  const analysis = payload?.analysis;
  if (!analysis || typeof analysis !== 'object') throw new Error('The analysis response was incomplete.');
  const details = payload?.details && typeof payload.details === 'object' ? payload.details : {};
  const severity = textValue(analysis.severity, 'unknown').toLowerCase();
  return {
    requestId: textValue(details.incident_id, textValue(payload.request_id, 'unavailable')),
    title: textValue(analysis.title, 'Incident analysis'),
    severity: ['low', 'medium', 'high', 'critical'].includes(severity) ? severity : 'unknown',
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

function Dot({ state = 'neutral' }) { return <span className={'dot dot-' + state} aria-hidden="true" />; }
function Badge({ children, tone = 'neutral' }) { return <span className={'badge badge-' + tone}><Dot state={tone} />{children}</span>; }
function NumberMetric({ label, value, detail }) { return <div className="overview-metric"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>; }
function Icon({ name }) {
  const paths = { overview: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>, investigate: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>, remediation: <><path d="M12 3v9" /><path d="m8 8 4 4 4-4" /><path d="M5 15v4h14v-4" /></>, experiments: <><path d="M5 19V5" /><path d="M5 19h14" /><path d="m8 15 3-4 3 2 4-5" /></>, demo: <><path d="M4 7h16v13H4z" /><path d="M8 7V4h8v3M8 12h8M8 16h5" /></>, kubernetes: <><path d="m12 3 7.8 4.5v9L12 21l-7.8-4.5v-9z" /><circle cx="12" cy="12" r="3" /></>, mlflow: <><path d="M5 19V9" /><path d="M5 13h5V7h5V3" /><circle cx="17" cy="3" r="2" /></>, menu: <><path d="M4 7h16M4 12h16M4 17h16" /></> }[name] || <circle cx="12" cy="12" r="8" />;
  return <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths}</svg>;
}

function Header({ runtime }) {
  const status = runtime.cluster === 'connected' ? 'CONNECTED' : runtime.cluster === 'degraded' ? 'DEGRADED' : runtime.cluster === 'checking' ? 'CHECKING' : 'OFFLINE';
  const tone = runtime.cluster === 'connected' ? 'good' : runtime.cluster === 'checking' ? 'warn' : 'bad';
  return <header className="topbar"><div className="topbar-inner"><button className="mobile-menu" type="button" onClick={runtime.onMenu} aria-label="Open navigation"><Icon name="menu" /></button><a className="brand" href="/" aria-label="AtlasAI home"><span className="brand-mark">AT</span><span><strong>AtlasAI</strong><small>Autonomous incident response</small></span></a><div className="topbar-status"><Badge tone={tone}>{status}</Badge><span className="status-divider" /><span className="ai-status"><Dot state={runtime.lora ? 'good' : 'warn'} /> AI {runtime.lora ? 'READY' : 'UNAVAILABLE'}</span><span className="mode-label">{runtime.mode === 'execute' ? 'CONTROLLED CLUSTER' : 'READ ONLY'}</span></div></div></header>;
}

function Overview({ runtime, cluster, observation, clusterError, onInspect }) {
  const observed = observation?.connection === 'connected' || cluster?.connection === 'connected';
  const tone = observed ? 'good' : runtime.cluster === 'checking' ? 'warn' : 'bad';
  const health = observation?.health?.status || (cluster ? 'observed' : 'offline');
  return <aside className="overview-panel" aria-label="Operations overview">
    <div className="overview-heading"><p className="eyebrow">OVERVIEW</p><span className="live-label"><Dot state={tone} /> LIVE STATE</span></div>
    <section className="overview-section"><div className="overview-section-title"><h2>Cluster</h2><Badge tone={tone}>{humanize(health)}</Badge></div><p className="overview-copy">{runtime.cluster === 'connected' ? runtime.namespace + ' / ' + resourceName(runtime.workload) : clusterError || 'Kubernetes observation is offline.'}</p><button className="quiet-button" type="button" onClick={onInspect}>Inspect state <span aria-hidden="true">→</span></button></section>
    <section className="overview-section"><p className="eyebrow">CAPACITY</p><div className="metric-grid"><NumberMetric label="Deployments" value={cluster ? finite(cluster.deployments_available) + ' / ' + finite(cluster.deployments_total) : '—'} detail="available / desired" /><NumberMetric label="Pods" value={cluster ? finite(cluster.pods_ready) + ' / ' + finite(cluster.pods_total) : '—'} detail="ready / observed" /></div></section>
    <section className="overview-section overview-note"><p className="eyebrow">CONTROL POSTURE</p><p>{runtime.mode === 'execute' ? 'Namespace-scoped actions require the safety gate and operator approval.' : 'Read-only until an approved Kubernetes executor is connected.'}</p></section>
    <div className="overview-footer"><span className="footer-key">MCP</span><span>Structured operational boundary</span></div>
  </aside>;
}

function ClusterHealthProof({ runtime, cluster, observation, clusterError, onInspect }) {
  const connected = observation?.connection === 'connected' || cluster?.connection === 'connected';
  const configured = runtime.cluster === 'connected';
  const health = observation?.health?.status || (cluster ? 'observed' : 'offline');
  const title = connected ? (health === 'degraded' ? 'Workload health needs attention' : 'Workload health observed') : configured ? 'Live observation unavailable' : 'Control plane unavailable';
  return <section className="cluster-proof command-card" aria-label="Live cluster proof"><div className="proof-heading"><div><p className="eyebrow">LIVE PROOF</p><h2>{title}</h2></div><Badge tone={connected ? healthTone(health) : configured ? 'warn' : 'bad'}>{connected ? humanize(health) : configured ? 'Unverified' : 'Offline'}</Badge></div><p className="proof-message">{connected ? 'Namespace ' + runtime.namespace + ', workload ' + resourceName(runtime.workload) + '. Counts below are read from the Kubernetes API observation boundary.' : clusterError || 'No Kubernetes observation was returned. Read-only analysis remains available.'}</p><div className="proof-grid"><NumberMetric label="Deployments" value={cluster ? finite(cluster.deployments_available) + ' / ' + finite(cluster.deployments_total) : '—'} detail="available / desired" /><NumberMetric label="Pods" value={cluster ? finite(cluster.pods_ready) + ' / ' + finite(cluster.pods_total) : '—'} detail="ready / observed" /></div>{!connected && <div className="offline-proof"><Dot state={configured ? 'warn' : 'bad'} /><span>{configured ? 'The runtime is configured, but a live API observation has not been verified.' : 'Control-plane offline is distinct from a workload health failure.'}</span></div>}{connected && observation?.health?.status === 'degraded' && <div className="offline-proof workload-warning"><Dot state="bad" /><span>The cluster is reachable; this workload is unhealthy from observed replicas, readiness, restarts, or events.</span></div>}<button className="quiet-button" type="button" onClick={onInspect}>Inspect current cluster <span aria-hidden="true">→</span></button><div className="execution-state"><span className="footer-key">{runtime.mode === 'execute' ? 'EXECUTE' : 'READ ONLY'}</span><span>{runtime.mode === 'execute' ? 'Mutations are limited to the four safe MCP actions.' : 'Connect an approved executor to mutate the demo workload.'}</span></div></section>;
}

function ChangeProof({ proof }) {
  if (!proof?.before || !proof?.after) return null;
  const facts = (value) => ({
    pods: Array.isArray(value.pods) ? value.pods.filter((pod) => pod.ready).length + ' / ' + value.pods.length : '—',
    deployment: value.deployment ? finite(value.deployment.available_replicas) + ' / ' + finite(value.deployment.desired_replicas) : '—',
    service: value.service_available === true ? 'Available' : value.service_available === false ? 'Unavailable' : 'Not inspected',
    health: humanize(value.health?.status),
  });
  const before = facts(proof.before); const after = facts(proof.after);
  return <section className="change-proof command-card" aria-label="Incident change proof"><div className="proof-heading"><div><p className="eyebrow">INCIDENT EVIDENCE</p><h2>What changed?</h2></div><Badge tone={healthTone(proof.after.health?.status)}>Kubernetes confirmed</Badge></div><p className="proof-message">AtlasAI captured a real baseline, applied the controlled fault, and received a fresh observation from the Kubernetes API.</p><div className="change-grid"><div><p className="eyebrow">BEFORE</p><NumberMetric label="Ready pods" value={before.pods} detail="healthy baseline" /><NumberMetric label="Deployment" value={before.deployment} detail="available / desired" /><NumberMetric label="Health" value={before.health} /></div><span className="recovery-divider" aria-hidden="true">→</span><div><p className="eyebrow">AFTER FAULT</p><NumberMetric label="Ready pods" value={after.pods} detail="fresh observation" /><NumberMetric label="Deployment" value={after.deployment} detail="available / desired" /><NumberMetric label="Health" value={after.health} /></div></div><div className="source-note"><span className="footer-key">SOURCE</span><span>Kubernetes API observation · {formatTimestamp(proof.after.observed_at, 'Timestamp recorded by observer')}</span></div></section>;
}

function CommandHeader({ onInspect, disabled }) { return <div className="command-heading"><div><p className="eyebrow">INCIDENT COMMAND CENTER</p><h1>Make the next decision obvious.</h1><p className="command-lede">Combine a human signal with live observations, then let evidence and policy shape the response.</p></div><button className="outline-button" type="button" onClick={onInspect} disabled={disabled} title="Read the approved demo workload from Kubernetes">Inspect cluster</button></div>; }

function InvestigationComposer({ description, setDescription, validation, isBusy, onSubmit, onExample }) {
  const count = useMemo(() => description.length.toLocaleString() + ' / 4,000', [description.length]);
  return <section className="command-card composer" aria-labelledby="incident-form-title"><div className="card-kicker"><span className="step-index">01</span><div><p className="eyebrow">NEW INVESTIGATION</p><h2 id="incident-form-title">What is happening?</h2></div><span className="card-hint">Human signal</span></div><form onSubmit={onSubmit} noValidate><label htmlFor="incident-description">Incident description</label><textarea id="incident-description" value={description} onChange={(event) => setDescription(event.target.value.slice(0, 4000))} maxLength="4000" aria-describedby="incident-hint incident-count incident-validation" aria-invalid={Boolean(validation)} placeholder="Service, symptom, and what changed..." /><div className="field-meta"><span id="incident-hint">Specific signals improve retrieval and diagnosis.</span><output id="incident-count">{count}</output></div><p id="incident-validation" className="validation-message" role="alert" hidden={!validation}>{validation}</p><div className="composer-footer"><span className="advisory"><Dot state="good" /> Advisory until the policy gate passes</span><button className="primary-button" type="submit" disabled={isBusy || !description.trim()}>{isBusy ? 'Investigating...' : 'Analyze investigation'} <span aria-hidden="true">→</span></button></div></form><div className="example-row"><span>Try a signal</span>{EXAMPLES.map((example) => <button type="button" key={example.label} onClick={() => onExample(example.description)} disabled={isBusy} title={'Use ' + example.label.toLowerCase() + ' example'}>{example.label}</button>)}</div></section>;
}

function ConfirmationModal({ scenario, onConfirm, onCancel }) {
  const selected = SCENARIOS.find((item) => item.value === scenario) || SCENARIOS[0];
  return <div className="modal-backdrop" role="presentation"><section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-title"><p className="eyebrow">CONTROLLED CHANGE</p><h2 id="confirm-title">Inject a reversible incident?</h2><p>This will mutate only the approved <strong>ops-demo / checkout-api</strong> demo workload so the full detect → diagnose → recommend → verify loop can be observed.</p><dl className="modal-facts"><div><dt>Scenario</dt><dd>{selected.label}</dd></div><div><dt>Expected impact</dt><dd>{selected.impact}</dd></div><div><dt>Recovery</dt><dd>Rollback, restart, scale-to-one, or clear-safe-condition only.</dd></div></dl><p className="modal-note"><Dot state="warn" /> No production namespace or arbitrary command is reachable through this control.</p><div className="modal-actions"><button className="quiet-button" type="button" onClick={onCancel}>Cancel</button><button className="primary-button" type="button" onClick={onConfirm}>Inject controlled incident</button></div></section></div>;
}

function SimulationPanel({ enabled, busy, scenario, setScenario, auto, setAuto, onInject, target = 'ops-demo / checkout-api' }) {
  return <section className="command-card simulation-card" aria-labelledby="simulation-title"><div className="simulation-copy"><p className="eyebrow">CONTROLLED DEMO ENVIRONMENT</p><h2 id="simulation-title">Prove the recovery loop</h2><p>Mutates only the allowlisted <code>{target}</code> workload. The API records the baseline, detects the fault, gathers evidence, and verifies recovery.</p></div><div className="simulation-controls"><label htmlFor="scenario">Failure type</label><select id="scenario" value={scenario} onChange={(event) => setScenario(event.target.value)} disabled={!enabled || busy}>{SCENARIOS.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select><label className="toggle-row"><input type="checkbox" checked={auto} onChange={(event) => setAuto(event.target.checked)} disabled={!enabled || busy} /><span>Auto-remediate after policy approval</span></label><button className="outline-button" type="button" onClick={onInject} disabled={!enabled || busy}>{busy ? 'Working...' : 'Inject controlled incident'} <span aria-hidden="true">→</span></button></div>{!enabled && <p className="simulation-warning"><Dot state="warn" /> Injection is disabled until the connected runtime reports execute mode.</p>}<div className="simulation-safety"><span className="footer-key">SAFETY</span><span>Simulation is opt-in, namespace-scoped, and never executes arbitrary kubectl.</span></div></section>;
}

function Timeline({ phase, result }) {
  const configured = result?.details?.steps || [];
  const items = configured.length ? configured : stages.map((name) => ({ name: name.toLowerCase(), status: 'pending' }));
  const active = ['starting', 'queued', 'running'].includes(phase);
  return <section className="timeline" aria-label="Investigation timeline"><div className="timeline-head"><p className="eyebrow">WORKFLOW</p><span>{active ? 'LIVE' : result ? 'COMPLETE' : 'READY'}</span></div><div className="timeline-rail">{items.map((item, index) => { const status = textValue(item?.status, 'pending'); const done = ['complete', 'verified', 'executed'].includes(status); const current = active && index === (phase === 'queued' ? 0 : Math.min(1, items.length - 1)); const label = humanize(item?.name, stages[index] || 'Step'); return <div className={'timeline-item ' + (done ? 'is-done' : current ? 'is-current' : '')} key={label + '-' + index}><span className="timeline-node"><Dot state={done ? 'good' : current ? 'warn' : 'neutral'} /></span><strong>{label}</strong><small>{humanize(status)}</small></div>; })}</div></section>;
}

function eventRelevance(event, value, mode) {
  const reason = textValue(event?.reason, '').toLowerCase();
  const type = textValue(event?.event_type || event?.type, '').toLowerCase();
  const message = textValue(event?.message, '').toLowerCase();
  const involved = textValue(event?.involved_object, '').toLowerCase();
  const workload = textValue(value?.workload, '').toLowerCase();
  const priority = textValue(event?.priority, 'contextual');
  const critical = /failed|warning|crashloopbackoff|imagepull|errimage|unhealthy|backoff|not.?ready|unschedul|probe|denied|error/.test(reason + ' ' + type + ' ' + message);
  const recovery = /available|ready|complete|successfulcreate|started|healthy|restored|scaled up/.test(reason + ' ' + message);
  const routine = /scalingreplicaset|successfulcreate|successfuldelete|killing/.test(reason + ' ' + message);
  let score = priority === 'incident_critical' || critical ? 100 : priority === 'recovery_critical' || recovery ? 35 : priority === 'routine' ? -10 : 10;
  if (routine && !critical) score -= 20;
  if (type === 'warning' || type === 'error') score += 35;
  if (involved === workload || involved.startsWith(workload + '-')) score += 25;
  if (mode === 'recovery') score += recovery ? 70 : critical ? 25 : 0;
  if (mode !== 'recovery' && recovery) score -= 10;
  const observed = Date.parse(value?.observed_at || '');
  const eventTime = Date.parse(event?.observed_at || event?.last_timestamp || event?.timestamp || '');
  const recent = observed && eventTime && Math.abs(observed - eventTime) < 5 * 60 * 1000;
  const stale = observed && eventTime && observed - eventTime > 15 * 60 * 1000;
  if (recent) score += 10;
  if (mode === 'current' && value?.health?.status === 'healthy' && stale && critical) score = -5;
  return score;
}

function rankKubernetesEvents(value, mode) {
  const events = Array.isArray(value?.events) ? value.events : [];
  return events.map((event, index) => ({ event, score: eventRelevance(event, value, mode), index })).sort((left, right) => right.score - left.score || right.index - left.index);
}

function KeySignals({ value }) {
  const deployment = value?.deployment;
  const pods = Array.isArray(value?.pods) ? value.pods : [];
  const readyPods = pods.filter((pod) => pod.ready).length;
  const service = value?.service_available === true ? 'Available' : value?.service_available === false ? 'Unavailable' : 'Not inspected';
  const rollout = deployment?.rollout_complete ? 'Complete' : deployment ? 'Degraded' : 'Not observed';
  return <div className="key-signals"><p className="eyebrow">KEY KUBERNETES SIGNALS</p><div className="key-signal-grid"><NumberMetric label="Health" value={humanize(value?.health?.status, 'Unknown')} /><NumberMetric label="Ready replicas" value={deployment ? `${finite(deployment.ready_replicas)} / ${finite(deployment.desired_replicas)}` : '—'} /><NumberMetric label="Pod readiness" value={pods.length ? `${readyPods} / ${pods.length}` : 'Not observed'} /><NumberMetric label="Service endpoints" value={service} /><NumberMetric label="Rollout" value={rollout} /></div>{value?.health?.reasons?.length > 0 && <ul className="signal-list compact-signals">{value.health.reasons.slice(0, 4).map((reason) => <li key={reason}>{reason}</li>)}</ul>}</div>;
}

function EventCards({ value, mode = 'current' }) {
  const allEvents = Array.isArray(value?.events) ? value.events : [];
  const ranked = rankKubernetesEvents(value, mode);
  const keyEvents = ranked.filter((item) => item.score > 30).slice(0, 4);
  const row = ({ event, score }, index, technical = false) => {
    const reason = humanize(event.reason, 'Kubernetes event');
    const critical = score >= 100 || textValue(event.event_type || event.type, '').toLowerCase() === 'warning';
    return <article className={'event-row ' + (critical ? 'is-critical' : mode === 'recovery' && score >= 80 ? 'is-recovery' : '')} key={(event.name || reason) + '-' + index}><div className="event-marker" aria-hidden="true">{critical ? '!' : mode === 'recovery' && score >= 80 ? '✓' : '·'}</div><div className="event-row-main"><div className="event-row-head"><strong>{reason}</strong><time>{formatTimestamp(event.observed_at || event.last_timestamp || event.timestamp, 'Recent')}</time></div><span>{textValue(event.involved_object, 'selected workload')}</span><p>{textValue(event.message, 'The observer recorded a workload event.')}</p>{critical && <small className="event-significance">Primary signal</small>}{technical && <details open><summary>Technical details</summary><dl className="event-details"><div><dt>Priority</dt><dd>{humanize(event.priority, 'contextual')}</dd></div><div><dt>Reason</dt><dd>{reason}</dd></div><div><dt>Type</dt><dd>{textValue(event.event_type || event.type, 'Not reported')}</dd></div><div><dt>Resource</dt><dd>Pod/Deployment {textValue(event.involved_object, value?.workload || 'selected workload')}</dd></div><div><dt>Namespace</dt><dd>{textValue(value?.namespace, 'Not reported')}</dd></div><div><dt>Source</dt><dd>Kubernetes Event API</dd></div><div><dt>Last observed</dt><dd>{formatTimestamp(event.observed_at || event.last_timestamp || event.timestamp, 'Not reported')}</dd></div><div><dt>Raw message</dt><dd>{textValue(event.message, 'Not reported')}</dd></div></dl></details>}</div></article>;
  };
  if (!allEvents.length) return null;
  return <section className="event-cards"><p className="eyebrow">KEY KUBERNETES EVIDENCE</p><p className="evidence-intro">These are the Kubernetes signals most relevant to the current workload-health decision.</p>{keyEvents.length > 0 && <div className="event-list">{keyEvents.map(row)}</div>}{keyEvents.length === 0 && <p className="evidence-intro">No warning, failure, or recovery signal is relevant to this observation.</p>}{allEvents.length > keyEvents.length && <details className="technical-history"><summary>View full Kubernetes event history ({allEvents.length})</summary><div className="event-list">{ranked.map((item, index) => row(item, index, true))}</div></details>}</section>;
}

function Observation({ title, value }) {
  if (!value) return <div className="empty-observation"><Dot state="neutral" /> No Kubernetes observation attached.</div>;
  const mode = /after|recovery/i.test(title) ? 'recovery' : /fault|observed state/i.test(title) ? 'incident' : 'current';
  return <div className="observation-block"><div className="observation-head"><div><p className="eyebrow">{title}</p><strong>{textValue(value.namespace, 'namespace')} / {resourceName(value.workload)}</strong></div><Badge tone={healthTone(value.health?.status)}>{humanize(value.health?.status)}</Badge></div><KeySignals value={value} /><EventCards value={value} mode={mode} /><div className="source-note"><span className="footer-key">FRESH READ</span><span>{textValue(value.source, 'Kubernetes API')} · {textValue(value.observation_id, 'observation id')} · {formatTimestamp(value.observed_at)}</span></div></div>;
}

function ActionPreview({ remediation }) {
  const preview = remediation?.execution_preview;
  if (!preview) return <div className="remediation-scope"><span>Operation</span><strong>No executable operation is available until a connected observation and policy-approved action exist.</strong><span>Safety</span><strong>Allowlist + evidence gate</strong></div>;
  const evidence = Array.isArray(preview.rag_evidence) ? preview.rag_evidence : [];
  const commands = Array.isArray(preview.rag_commands) ? preview.rag_commands : [];
  return <div className="operation-preview"><div className="operation-head"><div><p className="eyebrow">EXECUTION PREVIEW</p><strong>What AtlasAI will execute</strong></div><Badge tone="neutral">MCP -&gt; Kubernetes API</Badge></div><dl className="operation-facts"><div><dt>Recommended action</dt><dd>{humanize(preview.action)}</dd></div><div><dt>Why</dt><dd>{textValue(preview.operation, 'A policy-approved operation for the observed workload.')}</dd></div><div><dt>Target</dt><dd>{textValue(preview.target, 'Allowlisted workload')}</dd></div><div><dt>Namespace</dt><dd>{textValue(preview.namespace, 'Not observed')}</dd></div><div><dt>Resource</dt><dd>{textValue(preview.resource_type, 'Kubernetes resource')}</dd></div><div><dt>Category</dt><dd>{textValue(preview.category, 'Not reported')}</dd></div><div><dt>Execution method</dt><dd>{textValue(preview.execution_method, 'MCP safe action through the Kubernetes Python client')}</dd></div><div><dt>Actual API</dt><dd><code>{textValue(preview.api_method, 'Kubernetes client method not reported')}</code></dd></div><div><dt>Safety</dt><dd>{textValue(preview.policy_result, 'Policy decision not reported')}</dd></div><div><dt>Approval</dt><dd>{preview.approval_required ? 'Operator approval required' : 'Approval satisfied by the permitted execution mode'}</dd></div></dl><details className="operation-technical"><summary>Technical execution</summary><div className="technical-execution"><p className="eyebrow">ACTUAL OPERATION</p><code>{textValue(preview.api_method, 'Kubernetes client method not reported')}</code><p className="eyebrow">EXECUTION PATH</p><code>{textValue(preview.execution_method, 'MCP safe action through the Kubernetes Python client')}</code></div></details>{evidence.length > 0 && <details className="preview-evidence"><summary>Retrieved runbook guidance ({evidence.length})</summary><p className="preview-source-note">Source: Retrieved Runbook · Purpose: reference / remediation guidance · Execution: not executed directly.</p><ul>{evidence.map((item, index) => <li key={(item.id || item.title || 'evidence') + '-' + index}><div><strong>{textValue(item.title, 'Retrieved source')}</strong><span>{Math.round(clamp(item.score) * 100)}% match</span></div><p>{textValue(item.excerpt, 'No excerpt supplied.')}</p></li>)}</ul></details>}{commands.length > 0 ? <details className="preview-commands"><summary>RAG suggested commands ({commands.length})</summary><p className="preview-source-note">Source: Retrieved Runbook · Purpose: reference / remediation guidance · Execution: not executed directly.</p>{commands.map((command, index) => <code key={command + '-' + index}>{command}</code>)}</details> : <p className="preview-source-note">No executable command retrieved. The actual executor uses the Kubernetes API method shown above.</p>}<p className="operation-note"><Dot state="good" /> The executor calls the allowlisted Kubernetes client operation through MCP. AtlasAI does not execute arbitrary shell or kubectl commands.</p></div>;
}

function RecoveryProof({ result }) {
  const remediation = result.remediation || {};
  const before = result.details?.observation || remediation.before_observation;
  const after = remediation.after_observation;
  const verified = remediation.status === 'verified' && remediation.health_verified;
  if (!after && !before) return null;
  return <section className="recovery-proof"><div className="proof-heading"><div><p className="eyebrow">RECOVERY PROOF</p><h3>{verified ? 'Kubernetes confirmed the workload is healthy' : 'Observed state transition'}</h3></div><Badge tone={verified ? 'good' : 'warn'}>{verified ? 'VERIFIED' : 'PENDING'}</Badge></div><div className="recovery-columns"><Observation title="Fault observed" value={before} /><span className="recovery-divider" aria-hidden="true">→</span><Observation title="After operation" value={after} /></div><details className="how-know" open={verified}><summary>How do we know?</summary><p>{verified ? 'The post-action observation reports a connected cluster, ready replicas matching desired replicas, ready pods, and no failing health reasons. This is a fresh Kubernetes read, not a UI status flag.' : 'Verification is pending until the executor returns a fresh Kubernetes observation.'}</p></details></section>;
}

function RemediationCard({ result, job, onApprove }) {
  const remediation = result.remediation || {};
  const verified = remediation.status === 'verified' && remediation.health_verified;
  const waiting = remediation.status === 'awaiting_approval';
  const actionName = textValue(remediation.action, result.actions[0]?.text || 'Review evidence');
  return <section className={'remediation-card ' + (verified ? 'is-resolved' : waiting ? 'is-waiting' : '')}><div className="remediation-head"><div><p className="eyebrow">{verified ? 'INCIDENT RESOLVED' : 'RECOMMENDED REMEDIATION'}</p><h2>{verified ? 'Recovery verified by Kubernetes' : humanize(actionName)}</h2></div><Badge tone={verified ? 'good' : waiting ? 'warn' : remediation.status === 'failed' ? 'bad' : 'neutral'}>{verified ? 'RESOLVED' : humanize(remediation.status, 'REVIEW')}</Badge></div><p className="remediation-message">{textValue(remediation.message, 'Review the ranked plan and confirm the safest next step.')}</p><ActionPreview remediation={remediation} />{waiting && job?.job_id && <button className="primary-button" type="button" onClick={onApprove}>Approve and execute <span aria-hidden="true">→</span></button>}{verified && <p className="verified-note"><Dot state="good" /> Health verification passed from the cluster observation.</p>}</section>;
}

function EvidenceList({ evidence }) {
  if (!evidence.length) return <div className="empty-state">No sufficiently relevant operational evidence found.</div>;
  return <ul className="evidence-list">{evidence.map((item, index) => <li key={(item.id || item.title || 'source') + '-' + index}><div><span className="evidence-kind">{humanize(item.kind, 'Runbook')}</span><strong>{textValue(item.title, 'Source')}</strong></div><span className="evidence-score">{Math.round(clamp(item.score) * 100)}%</span><p>{textValue(item.excerpt, 'No excerpt supplied.')}</p><details><summary>Why this evidence was relevant</summary><p>Retrieved for the incident symptoms and triage category; score reflects the bounded local retrieval ranking.</p></details></li>)}</ul>;
}

function ResultView({ result, job, onApprove, onNew, onCopy, copyState }) {
  const details = result.details || {};
  const limitation = result.limitations.length ? result.limitations.join(' · ') : 'No additional limitation reported.';
  const knowledge = result.knowledge || {};
  return <section className="results" aria-labelledby="result-title"><div className="result-title-row"><div><p className="eyebrow">INCIDENT REVIEW · {result.requestId.slice(0, 12)}</p><h2 id="result-title" tabIndex="-1">{result.title}</h2></div><Badge tone={result.severity === 'high' || result.severity === 'critical' ? 'bad' : result.severity === 'medium' ? 'warn' : 'good'}>{humanize(result.severity)}</Badge></div><div className="diagnosis-grid"><section className="command-card diagnosis-card"><div className="diagnosis-lead"><div><p className="eyebrow">PROBABLE ROOT CAUSE</p><p className="cause-lead">{result.causes[0] || 'No likely cause was isolated.'}</p></div><div className="confidence"><span>Confidence</span><strong>{Math.round(result.confidence * 100)}%</strong><div className="confidence-bar"><span style={{ width: Math.round(result.confidence * 100) + '%' }} /></div></div></div><div className="diagnosis-columns"><div><p className="eyebrow">OBSERVED SIGNALS</p><ul className="signal-list">{(result.causes.length ? result.causes : ['No additional signal was reported.']).map((cause) => <li key={cause}>{cause}</li>)}</ul></div><div><p className="eyebrow">RANKED PLAN</p><ol className="plan-list">{(result.actions.length ? result.actions : [{ text: 'Review evidence with an operator.' }]).map((action, index) => <li key={(action.text || 'action') + '-' + index}><span className="plan-rank">{action.rank || index + 1}</span><div><strong>{textValue(action.text, 'Review with an operator.')}</strong>{action.evidence_ids?.length > 0 && <small>Evidence: {action.evidence_ids.join(', ')}</small>}</div></li>)}</ol></div></div></section><RemediationCard result={result} job={job} onApprove={onApprove} /></div><RecoveryProof result={result} /><div className="evidence-layout"><section className="command-card evidence-card"><div className="card-kicker"><span className="step-index">02</span><div><p className="eyebrow">EVIDENCE</p><h2>Why this result is grounded</h2></div><span className="card-hint">{result.evidence.length} sources</span></div><div className="retrieval-proof"><strong>{humanize(knowledge.retrieval_method, 'Retrieval unavailable')}</strong><span>Top match {Math.round(clamp(knowledge.best_score) * 100)}% · {finite(knowledge.candidate_count)} candidates retained</span></div><EvidenceList evidence={result.evidence} /></section><section className="command-card observation-card"><div className="card-kicker"><span className="step-index">03</span><div><p className="eyebrow">KUBERNETES OBSERVATION</p><h2>Infrastructure facts</h2></div></div><Observation title="Observed state" value={details.observation} /></section></div><details className="command-card technical-details"><summary><span><span className="eyebrow">TECHNICAL DETAILS</span><strong>Agent trace, MCP calls, and safety decisions</strong></span><span aria-hidden="true">+</span></summary><div className="technical-grid"><div><p className="eyebrow">LANGGRAPH TRACE</p><ul className="technical-list">{(details.steps || []).map((step) => <li key={step.name}><Dot state={step.status === 'complete' ? 'good' : 'warn'} /><span>{humanize(step.name)}</span><strong>{humanize(step.status)}</strong></li>)}</ul></div><div><p className="eyebrow">MCP TOOLS</p><ul className="technical-list">{(details.tools || []).length ? details.tools.map((tool) => <li key={tool.name}><Dot state={tool.status === 'complete' ? 'good' : 'warn'} /><details className="tool-detail"><summary>{humanize(tool.name)}</summary><p>{toolPurpose(tool.name)}</p><small>Result: {humanize(tool.status)}</small><small>Source: MCP structured content</small></details></li>) : <li><Dot /><span>No operational tools called</span></li>}</ul></div><div><p className="eyebrow">CONTEXT BOUNDARIES</p><ul className="context-list"><li>TriageContext <span>incident + observation</span></li><li>KnowledgeContext <span>triage + focused signals</span></li><li>ResolutionContext <span>triage + selected evidence</span></li><li>RemediationContext <span>policy-approved action</span></li></ul></div><div><p className="eyebrow">LIMITATIONS</p><p className="technical-copy">{limitation}</p><p className="technical-copy">Classifier: {humanize(details.models?.classifier)} · Resolution: {humanize(details.models?.resolution)}</p><p className="technical-copy">MLflow is training-only; no runtime tracking run is implied.</p></div></div></details><div className="result-actions"><button className="outline-button" type="button" onClick={onCopy}>{copyState || 'Copy summary'}</button><button className="quiet-button" type="button" onClick={onNew}>Start new investigation</button></div></section>;
}

function ProgressCard({ phase, job, onCancel }) {
  const queued = phase === 'queued';
  const copy = queued ? 'Waiting for the bounded analysis slot.' : phase === 'starting' ? 'Connecting to the analysis service.' : 'Reading the incident, cluster signals, and supporting evidence.';
  return <section className="command-card progress-card" aria-live="polite" aria-busy="true"><div className="spinner" aria-hidden="true" /><div><p className="eyebrow">{queued ? 'QUEUED' : 'INVESTIGATING'}</p><h2>{copy}</h2><p className="progress-copy">{job?.position > 0 ? 'Queue position ' + job.position + '.' : 'The workflow is read-only until the safety gate is evaluated.'}</p><button className="quiet-button" type="button" onClick={onCancel}>Cancel request</button></div></section>;
}

function formatTimestamp(value, fallback = 'Not verified yet') {
  if (!value) return fallback;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return fallback;
  return parsed.toLocaleString(undefined, { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).replace(',', ' ·');
}

const NAV_ITEMS = [
  { id: 'overview', label: 'Overview', icon: 'overview', group: 'Monitor' },
  { id: 'investigate', label: 'Investigate', icon: 'investigate', group: 'Operate' },
  { id: 'remediation', label: 'Remediation', icon: 'remediation', group: 'Operate' },
  { id: 'experiments', label: 'Experiments', icon: 'experiments', group: 'Learn' },
  { id: 'demo', label: 'Demo Environment', icon: 'demo', group: 'Demo' },
];

function Sidebar({ page, setPage, runtime, open, onClose }) {
  const apiTone = runtime.cluster === 'connected' ? 'good' : runtime.cluster === 'checking' ? 'warn' : 'bad';
  return <><div className={'sidebar-backdrop ' + (open ? 'is-open' : '')} onClick={onClose} /><aside className={'sidebar ' + (open ? 'is-open' : '')} aria-label="Primary navigation"><div className="sidebar-brand"><span className="brand-mark">AT</span><span><strong>AtlasAI</strong><small>Incident command</small></span><button className="sidebar-close" type="button" onClick={onClose} aria-label="Close navigation">Close</button></div>{['Monitor', 'Operate', 'Learn', 'Demo'].map((group) => <div className="nav-group" key={group}><p className="nav-group-label">{group}</p>{NAV_ITEMS.filter((item) => item.group === group).map((item) => <button className={'nav-item ' + (page === item.id ? 'is-active' : '')} type="button" key={item.id} onClick={() => { setPage(item.id); onClose(); }}><Icon name={item.icon} /><span>{item.label}</span></button>)}</div>)}<div className="sidebar-status"><p className="nav-group-label">System status</p><div><Icon name="kubernetes" /><span>Kubernetes</span><Badge tone={apiTone}>{runtime.cluster === 'connected' ? 'Connected' : runtime.cluster === 'checking' ? 'Checking' : 'Unreachable'}</Badge></div><div><Icon name="mlflow" /><span>MLflow</span><Badge tone={runtime.mlflow === 'available' ? 'good' : 'neutral'}>{runtime.mlflow === 'available' ? 'Available' : 'Unavailable'}</Badge></div></div></aside></>;
}

function PageIntro({ eyebrow, title, children, action }) { return <div className="page-intro"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1>{children && <p className="page-lede">{children}</p>}</div>{action}</div>; }

function HealthHero({ runtime, cluster, observation, error, inspectionMeta, onInspect, onInvestigate }) {
  const connected = observation?.connection === 'connected' || cluster?.connection === 'connected';
  const health = observation?.health?.status || (cluster ? 'observed' : 'unknown');
  const workloadBad = connected && health === 'degraded';
  const title = !connected ? (runtime.cluster === 'connected' ? 'Kubernetes verification pending' : 'Kubernetes environment unreachable') : workloadBad ? 'Application workload unhealthy' : 'Kubernetes environment healthy';
  const tone = connected ? healthTone(health) : runtime.cluster === 'connected' ? 'warn' : 'bad';
  const ready = observation?.deployment ? `${finite(observation.deployment.ready_replicas)} / ${finite(observation.deployment.desired_replicas)}` : cluster ? `${finite(cluster.pods_ready)} / ${finite(cluster.pods_total)}` : '—';
  const available = observation?.deployment ? `${finite(observation.deployment.available_replicas)} / ${finite(observation.deployment.desired_replicas)}` : cluster ? `${finite(cluster.deployments_available)} / ${finite(cluster.deployments_total)}` : '—';
  const serviceState = observation?.service_available === true ? 'Available' : observation?.service_available === false ? 'Unavailable' : 'Not inspected';
  return <section className={'health-hero tone-' + tone}><div className="health-hero-head"><div><p className="eyebrow">KUBERNETES ENVIRONMENT</p><h2>{title}</h2></div><Badge tone={tone}>{connected ? humanize(health) : runtime.cluster === 'connected' ? 'Unverified' : 'Unreachable'}</Badge></div><p className="health-hero-copy">{connected ? (workloadBad ? 'The control plane is reachable, but the monitored workload is not currently available.' : 'The monitored namespace and workload passed the configured health policy.') : error || 'AtlasAI cannot verify the Kubernetes API until the connected runtime is available.'}</p><div className="health-metrics"><NumberMetric label="Ready pods" value={ready} detail="fresh observation" /><NumberMetric label="Available replicas" value={available} detail="deployment state" /><NumberMetric label="Service endpoints" value={serviceState} detail="Kubernetes endpoint read" /><NumberMetric label="Last verified" value={formatTimestamp(inspectionMeta?.checkedAt || observation?.observed_at || cluster?.observed_at, 'Awaiting inspection')} /></div><div className="health-actions"><button className="primary-button" type="button" onClick={onInspect}>Inspect current cluster</button>{workloadBad && <button className="outline-button" type="button" onClick={onInvestigate}>Investigate incident</button>}</div>{inspectionMeta && <details className="verification-response"><summary>Verification response</summary><dl><div><dt>HTTP-style status</dt><dd>{inspectionMeta.statusCode || 'Unavailable'}</dd></div><div><dt>Result</dt><dd>{humanize(health)}</dd></div><div><dt>Checked at</dt><dd>{formatTimestamp(inspectionMeta.checkedAt)}</dd></div><div><dt>Source</dt><dd>Kubernetes API</dd></div></dl></details>}</section>;
}

function OverviewPage({ runtime, cluster, observation, inspectionMeta, incidentProof, result, clusterError, onInspect, onInvestigate }) {
  return <div className="page-content"><PageIntro eyebrow="MONITOR" title="Overview">One authoritative view of the current Kubernetes environment and the active incident lifecycle.</PageIntro><HealthHero runtime={runtime} cluster={cluster} observation={observation || result?.details?.observation} inspectionMeta={inspectionMeta} error={clusterError} onInspect={onInspect} onInvestigate={onInvestigate} />{result && <section className="compact-incident command-card"><div className="proof-heading"><div><p className="eyebrow">ACTIVE INCIDENT</p><h2>{result.title}</h2></div><Badge tone={healthTone(result.severity === 'critical' || result.severity === 'high' ? 'degraded' : result.severity)}>{humanize(result.severity)}</Badge></div><p>{result.causes[0] || 'Investigation completed without a single isolated cause.'}</p><div className="compact-links"><button className="quiet-button" type="button" onClick={onInvestigate}>View investigation →</button><button className="quiet-button" type="button" onClick={() => onInvestigate('remediation')}>View remediation →</button></div></section>}{incidentProof && <ChangeProof proof={incidentProof} />}<section className="overview-lower"><section className="command-card overview-observation"><div className="card-kicker"><div><p className="eyebrow">LIVE PROOF</p><h2>What Kubernetes reported</h2></div><button className="quiet-button" type="button" onClick={onInspect}>Refresh</button></div><Observation title="Latest observation" value={observation || result?.details?.observation} /></section><section className="command-card overview-status"><p className="eyebrow">SYSTEM STATUS</p><div className="status-row"><Icon name="kubernetes" /><span>Kubernetes API</span><Badge tone={runtime.cluster === 'connected' ? 'good' : 'bad'}>{runtime.cluster === 'connected' ? 'Connected' : 'Unreachable'}</Badge></div><div className="status-row"><Icon name="mlflow" /><span>MLflow</span><Badge tone="neutral">Training only</Badge></div><p className="small-copy">Runtime incident handling does not create MLflow runs. Training metadata is tracked separately.</p></section></section></div>;
}

function InvestigatePage({ description, setDescription, validation, isBusy, onSubmit, onExample, phase, result, job, error, retry, reset, cancel, onApprove, onCopy, copyState }) {
  return <div className="page-content"><PageIntro eyebrow="OPERATE / INVESTIGATE" title="Investigate an incident">Start with a human signal. AtlasAI combines it with the latest observation, MCP evidence, RAG retrieval, and policy checks.</PageIntro><InvestigationComposer description={description} setDescription={setDescription} validation={validation} isBusy={isBusy} onSubmit={onSubmit} onExample={onExample} />{(isBusy || result || error) && <Timeline phase={phase} result={result} />}{isBusy && <ProgressCard phase={phase} job={job} onCancel={cancel} />}{error && <section className="command-card error-card" role="alert"><p className="eyebrow">INVESTIGATION PAUSED</p><h2>{error.message}</h2><p className="error-explanation">No success state was inferred. Retry after the reported boundary error is resolved.</p><div className="error-actions">{error.retryable && <button className="outline-button" type="button" onClick={retry}>Try again</button>}<button className="quiet-button" type="button" onClick={reset}>Start new investigation</button></div></section>}{result && phase === 'success' && <ResultView result={result} job={job} onApprove={onApprove} onNew={reset} onCopy={onCopy} copyState={copyState} />}</div>;
}

function RemediationPage({ result, job, onApprove, onNew }) {
  if (!result) return <div className="page-content"><PageIntro eyebrow="OPERATE / REMEDIATION" title="Remediation">A detailed action story appears here after an investigation has produced a policy-reviewed recommendation.</PageIntro><section className="empty-page command-card"><Icon name="remediation" /><h2>No remediation plan yet</h2><p>Investigate an incident first. Any action shown here will reference the same evidence and observation returned by that investigation.</p></section></div>;
  return <div className="page-content"><PageIntro eyebrow="OPERATE / REMEDIATION" title="Remediation">Follow the chain from evidence to a safe Kubernetes operation and independently verified recovery.</PageIntro><section className="story-grid"><section className="command-card story-card"><p className="eyebrow">WHAT HAPPENED</p><h2>{result.title}</h2><p>{result.causes[0] || 'Review the evidence below for the observed cause.'}</p><p className="story-arrow">Evidence → decision → policy → operation → verification</p></section><RemediationCard result={result} job={job} onApprove={onApprove} /></section><RecoveryProof result={result} /><section className="command-card remediation-evidence"><p className="eyebrow">EVIDENCE USED FOR THIS DECISION</p><EvidenceList evidence={result.evidence} /></section><button className="quiet-button" type="button" onClick={onNew}>Start new investigation</button></div>;
}

function ExperimentsPage({ runtime }) {
  const available = runtime.mlflow === 'available';
  return <div className="page-content"><PageIntro eyebrow="LEARN / EXPERIMENTS" title="MLflow experiments">Training telemetry is intentionally separate from runtime incident handling.</PageIntro><section className="experiment-card command-card"><div className="experiment-mark"><Icon name="experiments" /></div><p className="eyebrow">MLFLOW EXPERIMENT TRACKING</p><h2>{available ? 'Tracking endpoint available' : 'Tracking unavailable in this runtime'}</h2><p>{available ? 'This environment reports MLflow availability. Run metadata is read from the configured tracking backend.' : 'No MLflow server or mounted tracking store is attached to the connected runtime, so no run data is displayed.'}</p><div className="experiment-grid"><div><span>LoRA adapter</span><strong>{runtime.lora ? 'Available' : 'Not available'}</strong></div><div><span>Runtime role</span><strong>Incident handling</strong></div><div><span>Training runs</span><strong>{available ? 'See tracking backend' : 'Not reported'}</strong></div></div><p className="small-copy">The local verified training record is kept outside this production-style runtime. No metrics or run IDs are fabricated here.</p></section></div>;
}

function DemoPage({ runtime, cluster, observation, incidentProof, simulationEnabled, busy, scenario, setScenario, auto, setAuto, onInject, onInspect }) {
  return <div className="page-content"><PageIntro eyebrow="DEMO" title="Demo environment">A safe isolated Kubernetes environment for testing AtlasAI's detection and remediation loop.</PageIntro><section className="environment-card command-card"><div><p className="eyebrow">ENVIRONMENT</p><h2>{runtime.namespace}</h2><p className="small-copy">Cluster: Kubernetes API · Workload: {resourceName(runtime.workload)}</p></div><Badge tone={observation?.health?.status === 'degraded' ? 'bad' : observation?.connection === 'connected' ? 'good' : 'warn'}>{humanize(observation?.health?.status, 'Awaiting observation')}</Badge></section><SimulationPanel enabled={simulationEnabled} busy={busy} scenario={scenario} setScenario={setScenario} auto={auto} setAuto={setAuto} onInject={onInject} /><ChangeProof proof={incidentProof} /><section className="command-card demo-observation"><div className="card-kicker"><div><p className="eyebrow">CURRENT STATE</p><h2>Observed workload</h2></div><button className="quiet-button" type="button" onClick={onInspect}>Inspect current cluster</button></div><Observation title="Kubernetes API" value={observation} /></section></div>;
}

function App() {
  const [page, setPage] = useState('overview');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [description, setDescription] = useState('');
  const [validation, setValidation] = useState('');
  const [phase, setPhase] = useState('idle');
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [copyState, setCopyState] = useState('');
  const [runtime, setRuntime] = useState({ api: 'checking', cluster: 'checking', lora: false, mlflow: 'unavailable', mode: 'disabled', namespace: 'ops-demo', workload: 'checkout-api' });
  const [cluster, setCluster] = useState(null);
  const [observation, setObservation] = useState(null);
  const [inspectionMeta, setInspectionMeta] = useState(null);
  const [incidentProof, setIncidentProof] = useState(null);
  const [clusterError, setClusterError] = useState('');
  const [scenario, setScenario] = useState(SCENARIOS[0].value);
  const [autoRemediate, setAutoRemediate] = useState(false);
  const [confirmScenario, setConfirmScenario] = useState(false);
  const activeRef = useRef(null);
  const controllerRef = useRef(null);
  const timeoutRef = useRef(null);
  const cancelledRef = useRef(false);
  const lastDescriptionRef = useRef('');
  const isBusy = ['starting', 'queued', 'running'].includes(phase);
  const liveClusterConnected = observation?.connection === 'connected' || cluster?.connection === 'connected';
  const simulationEnabled = runtime.cluster === 'connected' && runtime.mode === 'execute' && liveClusterConnected;

  const loadCluster = async () => {
    try { const response = await apiFetch('/api/cluster/summary'); const payload = await response.json().catch(() => ({})); if (!response.ok) throw new Error(payload.message || 'Cluster summary unavailable.'); setCluster(payload); setRuntime((current) => ({ ...current, cluster: payload.connection === 'connected' ? 'connected' : 'offline' })); setClusterError(''); }
    catch (caught) { setCluster(null); setRuntime((current) => ({ ...current, cluster: 'offline' })); setClusterError(textValue(caught.message, 'Cluster connection unavailable.')); }
  };
  const inspectCluster = async () => {
    try { const response = await apiFetch('/api/cluster/observations'); const payload = await response.json().catch(() => ({})); setInspectionMeta({ statusCode: response.status, checkedAt: new Date().toISOString() }); if (!response.ok) throw new Error(payload.message || 'Cluster observations unavailable.'); setObservation(payload); setRuntime((current) => ({ ...current, cluster: payload.connection === 'connected' ? 'connected' : 'offline' })); setClusterError(''); await loadCluster(); }
    catch (caught) { setInspectionMeta((current) => current || ({ statusCode: 503, checkedAt: new Date().toISOString() })); setRuntime((current) => ({ ...current, cluster: 'offline' })); setClusterError(textValue(caught.message, 'Cluster observations unavailable.')); }
  };
  useEffect(() => {
    let active = true;
    apiFetch('/api/ready').then(async (response) => ({ ok: response.ok, payload: await response.json().catch(() => ({})) })).then(({ ok, payload }) => {
      if (!active) return;
      const optional = payload.optional || {};
      setRuntime({ api: ok && payload.status === 'ready' ? 'ready' : 'offline', cluster: optional.kubernetes || 'offline', lora: optional.lora === 'available', mlflow: optional.mlflow || 'unavailable', mode: optional.kubernetes_mode || 'disabled', namespace: optional.kubernetes_namespace || 'ops-demo', workload: optional.kubernetes_workload || 'checkout-api' });
      if (optional.kubernetes === 'connected') loadCluster();
    }).catch(() => { if (active) setRuntime((current) => ({ ...current, api: 'offline', cluster: 'offline' })); });
    return () => { active = false; controllerRef.current?.abort(); window.clearTimeout(timeoutRef.current); };
  }, []);
  const handleEvent = (event, id) => { if (activeRef.current !== id) return; if (event.status === 'queued') { setPhase('queued'); setJob((current) => ({ ...current, position: event.position })); } if (event.status === 'running') setPhase('running'); if (event.status === 'complete') setPhase('success'); };
  const statusOnce = async (url, signal, id) => { const response = await apiFetch(url, { signal }); const payload = await response.json().catch(() => ({})); if (activeRef.current !== id) return null; if (!response.ok) throw new Error('Investigation status unavailable.'); return payload; };
  const consumeJob = async (created, signal, id) => {
    let attempts = 0;
    while (activeRef.current === id) {
      try { const response = await apiFetch(created.events_url, { signal }); if (!response.ok) throw new Error('stream'); const streamed = await readNdjson(response, signal, (event) => handleEvent(event, id)); if (streamed) return streamed; const status = await statusOnce(created.status_url, signal, id); if (status?.result) return status.result; if (['complete', 'failed', 'cancelled'].includes(status?.status)) return null; throw new Error('stream-ended'); }
      catch (streamError) { if (streamError.name === 'AbortError' || cancelledRef.current || activeRef.current !== id) throw streamError; if (attempts >= 2) throw new Error('Live progress was interrupted. Try again.'); attempts += 1; const status = await statusOnce(created.status_url, signal, id); if (status?.result) return status.result; if (['failed', 'cancelled'].includes(status?.status)) return null; await new Promise((resolve) => window.setTimeout(resolve, 250 * (2 ** (attempts - 1)))); }
    }
    return null;
  };
  const executeJob = async (created, value) => {
    const id = requestId(); const controller = new AbortController();
    activeRef.current = id; controllerRef.current = controller; cancelledRef.current = false; lastDescriptionRef.current = value;
    setPhase(created.status === 'queued' ? 'queued' : 'starting'); setJob(created); setResult(null); setError(null); setValidation('');
    timeoutRef.current = window.setTimeout(() => controller.abort(), 125000);
    try { const rawResult = await consumeJob(created, controller.signal, id); if (rawResult) { const normalized = normalizeResult(rawResult); setResult(normalized); setObservation(normalized.details?.observation || null); setPhase('success'); } else if (!cancelledRef.current) throw new Error('The analysis service returned no result.'); }
    catch (caught) { if (caught.name === 'AbortError' && !cancelledRef.current) setError({ message: 'The investigation took too long. Try again.', retryable: true }); else if (activeRef.current === id) setError({ message: cancelledRef.current ? 'The investigation was cancelled.' : textValue(caught.message, 'Investigation failed. Try again.'), retryable: !cancelledRef.current }); setPhase('error'); }
    finally { if (activeRef.current === id) { window.clearTimeout(timeoutRef.current); timeoutRef.current = null; activeRef.current = null; controllerRef.current = null; } }
  };
  const runRequest = async (url, body, value) => {
    if (isBusy) return;
    setPhase('starting'); setError(null); setResult(null);
    try { const response = await apiFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); const payload = await response.json().catch(() => ({})); if (!response.ok) throw new Error(textValue(payload.error?.message, payload.message || 'The request could not be started.')); if (!payload.job_id) { if (payload.observation) setObservation(payload.observation); throw new Error(payload.message || 'No investigation job was created.'); } if (payload.incident?.observation) { setObservation(payload.incident.observation); if (payload.incident.baseline_observation) setIncidentProof({ before: payload.incident.baseline_observation, after: payload.incident.observation }); } await executeJob(payload, value); }
    catch (caught) { setError({ message: textValue(caught.message, 'The request could not be started.'), retryable: true }); setPhase('error'); }
  };
  const run = (value) => runRequest('/api/incidents/analyze/jobs', { description: value, client_request_id: requestId() }, value);
  const submit = (event) => { event.preventDefault(); const value = description.trim(); if (isBusy) return; if (!value) { setValidation('Describe the incident before starting an investigation.'); return; } run(value); };
  const beginInjection = () => { if (!simulationEnabled || isBusy) return; setConfirmScenario(true); };
  const inject = () => { setConfirmScenario(false); runRequest('/api/cluster/simulations/inject', { scenario, auto_remediate: autoRemediate }, 'Controlled ' + scenario + ' in ops-demo'); };
  const approve = async () => { if (!job?.job_id || isBusy) return; try { const response = await apiFetch('/api/incidents/analyze/jobs/' + encodeURIComponent(job.job_id) + '/approve', { method: 'POST' }); const payload = await response.json().catch(() => ({})); if (!response.ok) throw new Error(textValue(payload.error?.message, 'Approval could not be started.')); await executeJob(payload, lastDescriptionRef.current); } catch (caught) { setError({ message: textValue(caught.message, 'Approval could not be started.'), retryable: true }); setPhase('error'); } };
  const cancel = async () => { if (!job?.job_id || !activeRef.current) return; cancelledRef.current = true; try { await apiFetch('/api/incidents/analyze/jobs/' + encodeURIComponent(job.job_id), { method: 'DELETE' }); } catch { /* local cancellation still applies */ } controllerRef.current?.abort(); };
  const retry = () => { if (lastDescriptionRef.current) run(lastDescriptionRef.current); };
  const reset = () => { if (isBusy) return; setDescription(''); setValidation(''); setPhase('idle'); setJob(null); setResult(null); setError(null); setCopyState(''); setIncidentProof(null); };
  const copySummary = async () => { if (!result) return; const summary = result.title + '\nSeverity: ' + result.severity + '\nCause: ' + (result.causes[0] || 'Not isolated') + '\nRecommendation: ' + textValue(result.actions[0]?.text, 'Review with an operator.'); try { await navigator.clipboard.writeText(summary); setCopyState('Copied'); window.setTimeout(() => setCopyState(''), 1800); } catch { setCopyState('Copy unavailable'); } };
  const shared = { runtime, cluster, observation: observation || result?.details?.observation, inspectionMeta, incidentProof, clusterError, onInspect: inspectCluster };
  const content = page === 'overview' ? <OverviewPage {...shared} result={result} onInvestigate={(target) => setPage(target || 'investigate')} /> : page === 'investigate' ? <InvestigatePage description={description} setDescription={(value) => { setDescription(value); setValidation(''); }} validation={validation} isBusy={isBusy} onSubmit={submit} onExample={setDescription} phase={phase} result={result} job={job} error={error} retry={retry} reset={reset} cancel={cancel} onApprove={approve} onCopy={copySummary} copyState={copyState} /> : page === 'remediation' ? <RemediationPage result={result} job={job} onApprove={approve} onNew={reset} /> : page === 'experiments' ? <ExperimentsPage runtime={runtime} /> : <DemoPage {...shared} simulationEnabled={simulationEnabled} busy={isBusy} scenario={scenario} setScenario={setScenario} auto={autoRemediate} setAuto={setAutoRemediate} onInject={beginInjection} />;
  return <div className="app-frame"><Sidebar page={page} setPage={setPage} runtime={runtime} open={sidebarOpen} onClose={() => setSidebarOpen(false)} /><div className="app-main"><Header runtime={{ ...runtime, onMenu: () => setSidebarOpen(true) }} /><main className="page-shell">{content}</main><footer className="app-footer"><span>AtlasAI</span><span>Policy-gated infrastructure assistance</span></footer></div>{confirmScenario && <ConfirmationModal scenario={scenario} onConfirm={inject} onCancel={() => setConfirmScenario(false)} />}</div>;
}

createRoot(document.getElementById('root')).render(<App />);
