import { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const EXAMPLES = [
  { label: 'Payment outage', description: "Our payment API started returning 503 errors after today's deployment." },
  { label: 'Database pressure', description: 'Database connections are exhausted and checkout requests are timing out.' },
  { label: 'High latency', description: 'Our API p95 latency doubled after the latest release.' },
];

const QUEUE_COPY = {
  starting: ['Starting', 'OpsPilot is preparing the review.', 'Connecting to the analysis service.', 'A bounded analysis slot is reserved for this request.'],
  running: ['Running', 'OpsPilot is investigating the incident.', 'Checking the incident details and relevant evidence.', 'The review is bounded and read-only until a safety gate is passed.'],
};

const textValue = (value, fallback = '') => typeof value === 'string' ? value.trim() : fallback;
const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const clampConfidence = (value) => Math.min(1, Math.max(0, finite(value)));
const showStatus = (status) => ['queued', 'running', 'complete', 'failed', 'cancelled'].includes(status) ? status : null;
const requestId = () => window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;

function normalizeResult(payload) {
  const analysis = payload?.analysis;
  if (!analysis || typeof analysis !== 'object') throw new Error('The analysis response was incomplete.');
  const details = payload?.details && typeof payload.details === 'object' ? payload.details : {};
  const severityCandidate = textValue(analysis.severity, 'unknown').toLowerCase();
  const severity = ['low', 'medium', 'high', 'critical'].includes(severityCandidate) ? severityCandidate : 'unknown';
  const causes = Array.isArray(analysis.likely_causes) ? analysis.likely_causes.filter((item) => typeof item === 'string' && item.trim()).slice(0, 5) : [];
  const actions = Array.isArray(analysis.recommended_actions) ? analysis.recommended_actions.filter((item) => item && typeof item === 'object').slice(0, 5) : [];
  const evidence = Array.isArray(analysis.evidence) ? analysis.evidence.filter((item) => item && typeof item === 'object').slice(0, 8) : [];
  const confidence = clampConfidence(analysis.confidence);
  const knowledge = details.knowledge && typeof details.knowledge === 'object' ? details.knowledge : {};
  const remediation = details.remediation && typeof details.remediation === 'object' ? details.remediation : {};
  return {
    title: textValue(analysis.title, 'Incident analysis'),
    severity,
    causes,
    actions,
    evidence,
    confidence,
    limitations: Array.isArray(analysis.limitations) ? analysis.limitations.filter((item) => typeof item === 'string' && item.trim()).slice(0, 3) : [],
    details,
    knowledge,
    remediation,
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
    if (event.status === 'failed') {
      const failure = event.error && typeof event.error === 'object' ? event.error : {};
      const error = new Error(textValue(failure.message, 'OpsPilot could not complete the investigation. Try again.'));
      error.retryable = failure.retryable !== false;
      throw error;
    }
    if (event.status === 'cancelled') {
      const error = new Error('The investigation was cancelled.');
      error.retryable = false;
      throw error;
    }
  };
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, { stream: true });
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || '';
    lines.forEach(consume);
  }
  buffer += decoder.decode();
  consume(buffer);
  return result;
}

function ProgressCard({ phase, job, onCancel }) {
  const queued = phase === 'queued';
  const position = Number.isInteger(job?.position) && job.position > 0 ? job.position : null;
  const queueMessage = position === 1 ? "Another investigation is running. You're next in queue." : position ? `Your investigation is in the queue. Position ${position}.` : 'Your investigation is waiting for capacity.';
  const copy = queued ? ['Queued', 'Your investigation is waiting for capacity.', queueMessage, 'The request will start automatically when the active review finishes.'] : QUEUE_COPY[phase] || QUEUE_COPY.starting;
  return (
    <section className="card progress-card" aria-live="polite" aria-busy="true" aria-labelledby="progress-title">
      <div className="progress-mark" aria-hidden="true"><span className="progress-ring" /><span className="progress-core" /></div>
      <div className="progress-content">
        <div className="state-line"><p className="kicker">LIVE INVESTIGATION</p><span className="state-pill">{copy[0]}</span></div>
        <h2 id="progress-title">{copy[1]}</h2>
        <p className="progress-message" role="status">{copy[2]}</p>
        <p className="progress-job">{copy[3]}</p>
        <button className="text-button" type="button" onClick={onCancel}>Cancel request</button>
      </div>
    </section>
  );
}

function ErrorCard({ message, retryable, onRetry, onNew }) {
  return (
    <section className="card error-card" role="alert" aria-labelledby="error-title">
      <div className="error-mark" aria-hidden="true">!</div>
      <div className="error-content">
        <p className="kicker">INVESTIGATION PAUSED</p>
        <h2 id="error-title">We could not complete this review.</h2>
        <p className="error-message">{message}</p>
        <div className="error-actions">
          {retryable && <button className="secondary-button" type="button" onClick={onRetry}>Try again</button>}
          <button className="text-button" type="button" onClick={onNew}>New analysis</button>
        </div>
      </div>
    </section>
  );
}

function RuntimePill() {
  const [runtime, setRuntime] = useState({ state: 'checking', label: 'Checking service' });
  useEffect(() => {
    let active = true;
    fetch('/api/ready')
      .then(async (response) => ({ ok: response.ok, payload: await response.json().catch(() => ({})) }))
      .then(({ ok, payload }) => {
        if (!active) return;
        if (!ok || payload.status !== 'ready') setRuntime({ state: 'offline', label: 'Service unavailable' });
        else if (payload.optional?.lora === 'available') setRuntime({ state: 'ready', label: 'API ready · LoRA' });
        else setRuntime({ state: 'ready', label: 'API ready · safe fallback' });
      })
      .catch(() => { if (active) setRuntime({ state: 'offline', label: 'Service unavailable' }); });
    return () => { active = false; };
  }, []);
  return <span className="environment-pill"><span className={`status-dot ${runtime.state}`} aria-hidden="true" /> {runtime.label}</span>;
}

function StatusList({ items, empty = 'No status reported.' }) {
  if (!items?.length) return <li>{empty}</li>;
  return items.map((item, index) => (
    <li key={`${item.name || item.title || 'row'}-${index}`}><span>{textValue(item.name || item.title, 'Unknown')}</span><span>{textValue(item.status, 'Unknown')}</span></li>
  ));
}

function ResultView({ result, onNew, onCopy, copyState }) {
  const { details, knowledge, remediation } = result;
  const steps = Array.isArray(details.steps) ? details.steps.slice(0, 8) : [];
  const tools = Array.isArray(details.tools) ? details.tools.slice(0, 8) : [];
  const safety = Array.isArray(details.safety) ? details.safety.slice(0, 4) : [];
  const models = details.models && typeof details.models === 'object' ? details.models : {};
  const limitation = result.limitations.length ? result.limitations.join(' · ') : 'No additional limitation reported.';
  const posture = details.degraded || (remediation.status && remediation.status !== 'disabled') ? 'Review required' : 'Evidence-aware';
  const confidencePercent = Math.round(result.confidence * 100);
  const retrievalMethod = textValue(knowledge.retrieval_method, 'vector cosine + lexical rerank');
  const bestScore = Math.round(clampConfidence(knowledge.best_score) * 100);
  return (
    <section className="result-stack" aria-labelledby="result-title">
      <div className="result-header">
        <div><p className="kicker">INCIDENT ANALYSIS</p><h2 id="result-title" tabIndex="-1">{result.title}</h2></div>
        <span className={`severity-badge ${result.severity}`} title="Severity is a prioritization signal, not a diagnosis">{result.severity}</span>
      </div>
      <div className="card result-card">
        <div className="summary-head">
          <div><p className="field-label">PROBABLE ROOT CAUSE</p><p className="cause-text">{result.causes[0] || 'No likely cause was isolated.'}</p></div>
          <div className="confidence-block" title="Confidence reflects the available evidence and model agreement">
            <span className="field-label">CONFIDENCE</span><strong>{confidencePercent}%</strong>
            <div className="confidence-track" aria-hidden="true"><span style={{ width: `${confidencePercent}%` }} /></div>
            <span className="confidence-detail">Exact score: {result.confidence.toFixed(3)}</span>
          </div>
        </div>
        <div className="summary-grid">
          <div className="summary-column"><div className="column-heading"><span className="section-icon" aria-hidden="true">C</span><h3>Signals behind the diagnosis</h3></div>
            <ul className="cause-list">{(result.causes.length ? result.causes : ['No additional cause signal was reported.']).map((cause) => <li key={cause}>{cause}</li>)}</ul>
          </div>
          <div className="summary-column action-column"><div className="column-heading"><span className="section-icon" aria-hidden="true">N</span><h3>Recommended next step</h3></div>
            <p className="action-text">{textValue(result.actions[0]?.text, 'Collect more evidence and review the incident with an operator.')}</p>
            <ol className="action-list">{(result.actions.length ? result.actions : [{ text: 'Review the evidence with an operator before changing a live system.' }]).map((action, index) => <li key={`${action.text}-${index}`}>{textValue(action.text, 'Review with an operator.')}</li>)}</ol>
          </div>
        </div>
        <div className="metric-row">
          <div className="metric"><span className="metric-label">Evidence selected</span><strong>{result.evidence.length} relevant source{result.evidence.length === 1 ? '' : 's'}</strong></div>
          <div className="metric"><span className="metric-label">Review posture</span><strong>{posture}</strong></div>
          <div className="metric metric-wide"><span className="metric-label">Important limitation</span><span className="limitation">{limitation}</span></div>
        </div>
        <div className="result-footer"><p className="result-note"><span className="result-note-dot" aria-hidden="true" /> Verify the evidence before changing a live system.</p><div className="result-actions"><button className="secondary-button" type="button" onClick={onCopy}>{copyState || 'Copy summary'}</button><button className="secondary-button" type="button" onClick={onNew}>New analysis</button></div></div>
      </div>
      <details className="card details-card"><summary>View investigation details <span className="summary-hint">Flow, evidence, tools, and safety <span aria-hidden="true">↓</span></span></summary>
        <div className="details-body">
          <div className="detail-section"><div className="column-heading"><span className="section-icon" aria-hidden="true">01</span><h3>Investigation flow</h3></div><ol className="steps-list"><StatusList items={steps} empty="No step status reported." /></ol></div>
          <div className="detail-section"><div className="column-heading"><span className="section-icon" aria-hidden="true">02</span><h3>Knowledge signal</h3></div><p className="detail-summary">{Math.max(0, Math.trunc(finite(knowledge.runbooks_retrieved)))} runbook sources · {Math.max(0, Math.trunc(finite(knowledge.historical_incidents)))} historical matches</p><div className="retrieval-proof"><span>Retrieval</span><strong>{retrievalMethod.replaceAll('_', ' ')}</strong><span>Top match {bestScore}% · {Math.max(0, Math.trunc(finite(knowledge.candidate_count)))} candidates retained</span></div><ul className="evidence-list">{result.evidence.length ? result.evidence.map((item, index) => <li key={`${item.id || item.title || 'evidence'}-${index}`}><div className="evidence-heading"><strong>{textValue(item.title, 'Source')}</strong><span className="evidence-score">{Math.round(clampConfidence(item.score) * 100)}% match</span></div><span>{textValue(item.excerpt, 'No excerpt supplied.')}</span></li>) : <li>No evidence was selected.</li>}</ul></div>
          <div className="detail-columns">
            <div className="detail-section"><div className="column-heading"><span className="section-icon" aria-hidden="true">03</span><h3>MCP tools</h3></div><ul className="status-list"><StatusList items={tools} empty="No tool status reported." /></ul></div>
            <div className="detail-section"><div className="column-heading"><span className="section-icon" aria-hidden="true">04</span><h3>Models</h3></div><ul className="status-list"><li><span>Classifier</span><span>{textValue(models.classifier, 'Unknown')}</span></li><li><span>Resolution</span><span>{textValue(models.resolution, 'Unknown')}</span></li><StatusList items={safety} /></ul></div>
            <div className="detail-section"><div className="column-heading"><span className="section-icon" aria-hidden="true">05</span><h3>Safety gate</h3></div><ul className="status-list"><li><span>Status</span><span>{textValue(remediation.status, 'disabled')}</span></li><li><span>Action</span><span>{textValue(remediation.action, 'none')}</span></li><li><span>Health</span><span>{remediation.health_verified ? 'verified' : 'not verified'}</span></li></ul></div>
          </div>
        </div>
      </details>
    </section>
  );
}

function App() {
  const [description, setDescription] = useState('');
  const [phase, setPhase] = useState('idle');
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [copyState, setCopyState] = useState('');
  const activeRef = useRef(null);
  const controllerRef = useRef(null);
  const timeoutRef = useRef(null);
  const cancelledRef = useRef(false);
  const lastDescriptionRef = useRef('');
  const isBusy = phase === 'starting' || phase === 'queued' || phase === 'running';
  const countLabel = useMemo(() => `${description.length.toLocaleString()} / 4,000`, [description.length]);

  useEffect(() => () => {
    controllerRef.current?.abort();
    window.clearTimeout(timeoutRef.current);
  }, []);

  const reset = () => {
    if (isBusy) return;
    setDescription(''); setResult(null); setError(null); setJob(null); setPhase('idle'); setCopyState('');
  };
  const handleEvent = (event, id) => {
    if (activeRef.current !== id) return;
    const status = showStatus(event.status);
    if (!status) throw new Error('Live progress returned an invalid state.');
    if (status === 'queued') { setPhase('queued'); setJob((current) => ({ ...current, position: event.position })); }
    if (status === 'running') setPhase('running');
    if (status === 'complete') setPhase('success');
  };
  const statusOnce = async (url, signal, id) => {
    const response = await fetch(url, { signal });
    const payload = await response.json().catch(() => ({}));
    if (activeRef.current !== id) return null;
    if (!response.ok) throw new Error('The investigation status is unavailable. Please try again.');
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
        if (attempts >= 2) throw new Error('Live progress was interrupted. Please try again.');
        attempts += 1;
        const status = await statusOnce(created.status_url, signal, id);
        if (status?.result) return status.result;
        if (['failed', 'cancelled'].includes(status?.status)) return null;
        await new Promise((resolve) => window.setTimeout(resolve, 250 * (2 ** (attempts - 1))));
      }
    }
    return null;
  };
  const run = async (value) => {
    const id = requestId();
    const controller = new AbortController();
    activeRef.current = id; controllerRef.current = controller; cancelledRef.current = false; lastDescriptionRef.current = value;
    setPhase('starting'); setJob(null); setError(null); setResult(null);
    timeoutRef.current = window.setTimeout(() => { controller.abort(); }, 125000);
    try {
      const response = await fetch('/api/incidents/analyze/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description: value, client_request_id: id }), signal: controller.signal });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const apiError = payload?.error && typeof payload.error === 'object' ? payload.error : {};
        const apiFailure = new Error(textValue(apiError.message, response.status === 429 ? 'The public demo is busy. Please try again shortly.' : 'OpsPilot could not complete this investigation. Try again.'));
        apiFailure.retryable = apiError.retryable !== false;
        throw apiFailure;
      }
      setJob(payload);
      const rawResult = await consumeJob(payload, controller.signal, id);
      if (rawResult) setResult(normalizeResult(rawResult));
      else if (!cancelledRef.current) throw new Error('The analysis service returned no result.');
    } catch (caught) {
      if (caught.name === 'AbortError' && !cancelledRef.current) {
        setError({ message: 'The investigation took too long. Try again in a moment.', retryable: true });
      } else if (activeRef.current === id) {
        const message = cancelledRef.current ? 'The investigation was cancelled.' : caught.name === 'TypeError' ? 'OpsPilot could not reach the analysis service. Check your connection and try again.' : textValue(caught.message, 'OpsPilot could not complete this investigation. Try again.');
        setError({ message, retryable: caught.retryable !== false && !cancelledRef.current });
      }
      setPhase('error');
    } finally {
      if (activeRef.current === id) { window.clearTimeout(timeoutRef.current); timeoutRef.current = null; activeRef.current = null; controllerRef.current = null; if (!result) setPhase((current) => current === 'starting' || current === 'queued' || current === 'running' ? 'idle' : current); }
    }
  };
  const submit = (event) => { event.preventDefault(); const value = description.trim(); if (!value || value.length > 4000 || isBusy) return; run(value); };
  const cancel = async () => { if (!job?.job_id || !activeRef.current) return; cancelledRef.current = true; try { await fetch(`/api/incidents/analyze/jobs/${encodeURIComponent(job.job_id)}`, { method: 'DELETE' }); } catch { /* local cancellation still applies */ } controllerRef.current?.abort(); };
  const retry = () => { if (!isBusy && lastDescriptionRef.current) run(lastDescriptionRef.current); };
  const copySummary = async () => {
    if (!result) return;
    const summary = `${result.title}\nSeverity: ${result.severity}\nProbable cause: ${result.causes[0] || 'Not isolated'}\nRecommendation: ${textValue(result.actions[0]?.text, 'Review with an operator.')}`;
    try { await navigator.clipboard.writeText(summary); setCopyState('Copied'); window.setTimeout(() => setCopyState(''), 1800); } catch { setCopyState('Copy unavailable'); }
  };

  return (
    <>
      <header className="topbar"><div className="topbar-inner"><a className="brand" href="/" aria-label="OpsPilot home"><span className="brand-mark" aria-hidden="true">OP</span><span className="brand-name">OpsPilot</span></a><div className="topbar-meta"><RuntimePill /><p className="topbar-note">AI incident investigation assistant</p></div></div></header>
      <main className="shell">
        <section className="intro" aria-labelledby="page-title"><div className="eyebrow-row"><p className="eyebrow">CONTROLLED INCIDENT REVIEW</p><span className="eyebrow-line" aria-hidden="true" /></div><h1 id="page-title">Clarity when systems drift.</h1><p className="lede">Describe a production signal and OpsPilot will organize the investigation, ground it in evidence, and suggest a cautious next step.</p><div className="capability-row" aria-label="Investigation stages"><span className="capability" title="Classifies the incident into a bounded category"><span className="capability-number">01</span> Classify</span><span className="capability" title="Checks runbooks, history, and connected MCP tools"><span className="capability-number">02</span> Investigate</span><span className="capability" title="Ranks a safe, evidence-backed next step"><span className="capability-number">03</span> Recommend</span><span className="capability" title="Runs only a simulated allowlisted action after the safety gate"><span className="capability-number">04</span> Verify</span></div></section>
        <section className="card composer-card" aria-labelledby="form-title"><div className="section-heading"><div><p className="kicker">START AN INVESTIGATION</p><h2 id="form-title">Describe what went wrong</h2></div><span className="section-note">A focused signal is easier to verify</span></div><form id="incident-form" onSubmit={submit} noValidate><div className="label-row"><label className="field-label input-label" htmlFor="incident-description">Incident description</label><span className="field-note" title="Mention the service, symptom, and what changed if you know them">Service + symptom + change</span></div><textarea id="incident-description" name="description" value={description} onChange={(event) => setDescription(event.target.value.slice(0, 4000))} maxLength="4000" aria-describedby="input-hint character-count" placeholder="Example: The checkout API started returning 503 errors after today's deployment." required /><div className="field-meta"><p id="input-hint" className="input-hint">Include the service, symptom, and what changed if you know them.</p><output id="character-count" className={description.length >= 3600 ? 'character-count is-near-limit' : 'character-count'}>{countLabel}</output></div><div className="action-row"><p className="trust-note"><span className="trust-icon" aria-hidden="true">i</span> Advisory only. Every action is checked before it can run.</p><button id="submit-button" className={`primary-button ${isBusy ? 'is-loading' : ''}`} type="submit" disabled={isBusy || !description.trim()}><span className="button-label">{isBusy ? 'Analyzing…' : 'Analyze incident'}</span><span className="button-arrow" aria-hidden="true">→</span></button></div></form><div className="examples" aria-label="Example incidents"><span className="examples-label">Try a signal</span>{EXAMPLES.map((example) => <button key={example.label} type="button" onClick={() => !isBusy && setDescription(example.description)} disabled={isBusy} title={`Populate a ${example.label.toLowerCase()} example`}>{example.label}</button>)}</div></section>
        {isBusy && <ProgressCard phase={phase} job={job} onCancel={cancel} />}
        {error && <ErrorCard message={error.message} retryable={error.retryable} onRetry={retry} onNew={reset} />}
        {result && phase === 'success' && <ResultView result={result} onNew={reset} onCopy={copySummary} copyState={copyState} />}
        <p className="sr-only" role="status" aria-live="polite">{result ? `Analysis complete: ${result.title}` : error?.message || ''}</p>
      </main>
      <footer className="footer"><div className="footer-inner"><span>OpsPilot</span><span>Advisory analysis for careful operators</span></div></footer>
    </>
  );
}

createRoot(document.getElementById('root')).render(<App />);
