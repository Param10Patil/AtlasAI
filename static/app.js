const form = document.querySelector('#incident-form');
const input = document.querySelector('#incident-description');
const submitButton = document.querySelector('#submit-button');
const progressPanel = document.querySelector('#progress-panel');
const errorPanel = document.querySelector('#error-panel');
const resultPanel = document.querySelector('#result-panel');
const errorMessage = document.querySelector('#error-message');
const progressMessage = document.querySelector('#progress-message');
const state = { phase: 'idle', activeRequestId: null, clientRequestId: null, controller: null, lastDescription: '', jobId: null, cancelled: false };

const phases = { idle: new Set(['starting']), starting: new Set(['queued', 'running', 'success', 'error', 'idle']), queued: new Set(['running', 'success', 'error', 'idle']), running: new Set(['success', 'error', 'idle']), success: new Set(['starting', 'idle']), error: new Set(['starting', 'idle']) };
const show = (element, visible) => { if (element) element.hidden = !visible; };
const transition = (next) => { if (state.phase === next || !phases[state.phase]?.has(next)) return false; state.phase = next; return true; };
const setLoading = (loading) => { if (submitButton) submitButton.disabled = loading; show(progressPanel, loading); if (progressPanel) progressPanel.setAttribute('aria-busy', String(loading)); };
const clear = (element) => { while (element?.firstChild) element.removeChild(element.firstChild); };
const text = (value, fallback = '') => typeof value === 'string' ? value : fallback;
const safeNumber = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const addRow = (list, left, right = '') => { const item = document.createElement('li'); const first = document.createElement('span'); first.textContent = text(left, 'Unknown'); item.appendChild(first); if (right !== '') { const second = document.createElement('span'); second.textContent = text(right, 'Unknown'); item.appendChild(second); } list.appendChild(item); };
const addEvidence = (list, item) => { const entry = document.createElement('li'); const title = document.createElement('strong'); title.textContent = text(item?.title, 'Source'); const excerpt = document.createElement('span'); excerpt.textContent = text(item?.excerpt); entry.append(title, excerpt); list.appendChild(entry); };

document.querySelectorAll('[data-example]').forEach((button) => button.addEventListener('click', () => { input.value = button.dataset.example || ''; input.dispatchEvent(new Event('input')); input.focus(); }));
input?.addEventListener('input', () => { const counter = document.querySelector('#character-count'); if (counter) counter.textContent = `${input.value.length} / 4,000`; });

const renderResult = (payload) => {
  const analysis = payload?.analysis || {};
  const details = payload?.details || {};
  document.querySelector('#result-title').textContent = text(analysis.title, 'Incident analysis');
  const severity = text(analysis.severity, 'unknown');
  const badge = document.querySelector('#severity-badge'); badge.textContent = severity; badge.className = `severity-badge ${severity}`;
  document.querySelector('#cause-text').textContent = text((analysis.likely_causes || [])[0], 'No likely cause was isolated.');
  const action = (analysis.recommended_actions || [])[0]; document.querySelector('#action-text').textContent = text(action?.text, 'Collect more evidence and review the incident with an operator.');
  document.querySelector('#confidence-value').textContent = `${Math.round(Math.min(1, Math.max(0, safeNumber(analysis.confidence))) * 100)}%`;
  const evidence = Array.isArray(analysis.evidence) ? analysis.evidence : []; const knowledge = details.knowledge || {};
  const runbooks = Math.max(0, Math.trunc(safeNumber(knowledge.runbooks_retrieved))); const history = Math.max(0, Math.trunc(safeNumber(knowledge.historical_incidents)));
  document.querySelector('#knowledge-summary').textContent = `${runbooks} runbook source${runbooks === 1 ? '' : 's'} retrieved · ${history} historical match${history === 1 ? '' : 'es'}`;
  document.querySelector('#evidence-count').textContent = `${evidence.length} relevant source${evidence.length === 1 ? '' : 's'}`;
  document.querySelector('#limitation-text').textContent = Array.isArray(analysis.limitations) ? analysis.limitations.filter((item) => typeof item === 'string').join(' · ') : '';
  const steps = document.querySelector('#steps-list'); clear(steps); (Array.isArray(details.steps) ? details.steps : []).forEach((step) => addRow(steps, step?.name, step?.status));
  const evidenceList = document.querySelector('#evidence-list'); clear(evidenceList); evidence.forEach((item) => addEvidence(evidenceList, item)); if (!evidence.length) addRow(evidenceList, 'No evidence was selected.');
  const tools = document.querySelector('#tools-list'); clear(tools); (Array.isArray(details.tools) ? details.tools : []).forEach((item) => addRow(tools, item?.name, item?.status)); if (!tools.children.length) addRow(tools, 'No tool status reported');
  const models = details.models || {}; const modelList = document.querySelector('#model-list'); clear(modelList); addRow(modelList, 'Classifier', models.classifier); addRow(modelList, 'Resolution', models.resolution); (Array.isArray(details.safety) ? details.safety : []).forEach((item) => addRow(modelList, item?.name, item?.status));
  const remediation = details.remediation || {}; const remediationList = document.querySelector('#remediation-list'); clear(remediationList); addRow(remediationList, 'Status', remediation.status || 'disabled'); addRow(remediationList, 'Action', remediation.action || 'none'); addRow(remediationList, 'Health', remediation.health_verified ? 'verified' : 'not verified');
  show(resultPanel, true);
};

const eventStatus = (event) => ['queued', 'running', 'complete', 'failed', 'cancelled'].includes(event?.status) ? event.status : null;
const applyEvent = (event, requestId) => {
  if (state.activeRequestId !== requestId) return null;
  const status = eventStatus(event); if (!status) throw new Error('Live progress returned an invalid state.');
  if (status === 'queued' && (state.phase === 'starting' || state.phase === 'queued')) { transition('queued'); const position = Number.isInteger(event.position) ? event.position : null; progressMessage.textContent = position && position > 0 ? `Another investigation is running. You are next in line (${position}).` : 'Your investigation is queued.'; }
  else if (status === 'running' && ['starting', 'queued', 'running'].includes(state.phase)) { transition('running'); progressMessage.textContent = 'Checking the incident details and relevant evidence.'; }
  else if (status === 'complete' && ['starting', 'queued', 'running'].includes(state.phase)) { transition('success'); return event.result || null; }
  else if (status === 'failed' && state.phase !== 'success') { transition('error'); throw new Error('OpsPilot could not complete the investigation. Please try again.'); }
  else if (status === 'cancelled' && state.phase !== 'success') { transition('error'); throw new Error('The investigation was cancelled.'); }
  return null;
};

const readEvents = async (response, requestId) => {
  if (!response.body) throw new Error('Live progress is unavailable. Please try again.');
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let result = null;
  const consume = (line) => { if (!line.trim()) return; let event; try { event = JSON.parse(line); } catch (_) { throw new Error('Live progress returned malformed data.'); } const value = applyEvent(event, requestId); if (value) result = value; };
  while (true) { const chunk = await reader.read(); if (chunk.done) break; buffer += decoder.decode(chunk.value, { stream: true }); const lines = buffer.split('\n'); buffer = lines.pop() || ''; lines.forEach(consume); }
  buffer += decoder.decode(); consume(buffer); return result;
};

const wait = (duration) => new Promise((resolve) => setTimeout(resolve, duration));
const statusOnce = async (url, requestId) => { const response = await fetch(url, { signal: state.controller.signal }); const payload = await response.json().catch(() => ({})); if (state.activeRequestId !== requestId) return null; if (!response.ok) throw new Error('The investigation status is unavailable. Please try again.'); return payload; };
const consumeJob = async (job, requestId) => {
  let attempts = 0; let current = job;
  while (state.activeRequestId === requestId) {
    try { const response = await fetch(current.events_url, { signal: state.controller.signal }); if (!response.ok) throw new Error('stream'); const result = await readEvents(response, requestId); if (result) return result; const status = await statusOnce(current.status_url, requestId); if (status?.result) return status.result; if (['complete', 'failed', 'cancelled'].includes(status?.status)) return null; throw new Error('stream-ended'); }
    catch (error) { if (error.name === 'AbortError' || state.phase === 'error') throw error; if (attempts >= 2) throw new Error('Live progress was interrupted. Please try again.'); attempts += 1; const status = await statusOnce(current.status_url, requestId); if (status?.result) return status.result; if (['failed', 'cancelled'].includes(status?.status)) return null; await wait(250 * (2 ** (attempts - 1))); current = { ...current, events_url: current.events_url, status_url: current.status_url }; }
  }
  return null;
};

const runAnalysis = async (description) => {
  const requestId = (window.crypto && typeof window.crypto.randomUUID === 'function') ? window.crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  state.activeRequestId = requestId; state.clientRequestId = requestId; state.controller = new AbortController(); state.jobId = null; state.lastDescription = description; state.cancelled = false; transition('starting'); show(errorPanel, false); show(resultPanel, false); setLoading(true);
  try {
    const response = await fetch('/api/incidents/analyze/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description, client_request_id: requestId }), signal: state.controller.signal });
    const payload = await response.json().catch(() => ({})); if (state.activeRequestId !== requestId) return; if (!response.ok) throw new Error(text(payload?.error?.message, 'OpsPilot could not complete the investigation.'));
    state.jobId = text(payload.job_id); const result = await consumeJob(payload, requestId); if (result) renderResult(result);
  } catch (error) { if (error.name === 'AbortError' && !state.cancelled) return; if (state.activeRequestId !== requestId) return; errorMessage.textContent = state.cancelled ? 'The investigation was cancelled.' : (error.message || 'Please try again.'); show(errorPanel, true); }
  finally { if (state.activeRequestId === requestId) { state.activeRequestId = null; state.controller = null; setLoading(false); if (state.phase === 'starting' || state.phase === 'queued' || state.phase === 'running') transition('idle'); } }
};

form?.addEventListener('submit', (event) => { event.preventDefault(); if (state.activeRequestId) return; const description = input.value.trim(); if (!description || description.length > 4000) { input.setCustomValidity(description ? 'Keep the incident under 4,000 characters.' : 'Describe the incident before analyzing.'); input.reportValidity(); return; } input.setCustomValidity(''); runAnalysis(description); });
document.querySelector('#retry-button')?.addEventListener('click', () => { if (!state.activeRequestId) runAnalysis(state.lastDescription || input.value.trim()); });
document.querySelector('#new-analysis')?.addEventListener('click', () => { show(resultPanel, false); input.value = ''; input.dispatchEvent(new Event('input')); input.focus(); transition('idle'); });
document.querySelector('#cancel-analysis')?.addEventListener('click', async () => { if (!state.jobId || !state.activeRequestId) return; state.cancelled = true; try { await fetch(`/api/incidents/analyze/jobs/${state.jobId}`, { method: 'DELETE' }); } catch (_) { /* cancellation remains local if the network is unavailable */ } state.controller?.abort(); });
