(function () {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const form = $('#incident-form');
  const input = $('#incident-description');
  const submitButton = $('#submit-button');
  const buttonLabel = $('.button-label');
  const progressPanel = $('#progress-panel');
  const progressState = $('#progress-state');
  const progressTitle = $('#progress-title');
  const progressMessage = $('#progress-message');
  const progressJob = $('#progress-job');
  const cancelButton = $('#cancel-analysis');
  const errorPanel = $('#error-panel');
  const errorMessage = $('#error-message');
  const retryButton = $('#retry-button');
  const resultPanel = $('#result-panel');
  const resultTitle = $('#result-title');
  const validationMessage = $('#validation-message');
  const announcement = $('#announcement');
  const characterCount = $('#character-count');
  const exampleButtons = Array.from(document.querySelectorAll('[data-example]'));

  const state = {
    phase: 'idle',
    activeRequestId: null,
    clientRequestId: null,
    controller: null,
    timeoutId: null,
    lastDescription: '',
    jobId: null,
    cancelled: false,
    timedOut: false,
  };

  const phases = {
    idle: new Set(['starting']),
    starting: new Set(['queued', 'running', 'success', 'error', 'idle']),
    queued: new Set(['running', 'success', 'error', 'idle']),
    running: new Set(['success', 'error', 'idle']),
    success: new Set(['starting', 'idle']),
    error: new Set(['starting', 'idle']),
  };

  const show = (element, visible) => { if (element) element.hidden = !visible; };
  const clear = (element) => { while (element?.firstChild) element.removeChild(element.firstChild); };
  const stringValue = (value, fallback = '') => typeof value === 'string' ? value.trim() : fallback;
  const finiteNumber = (value, fallback = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  const announce = (message) => {
    if (!announcement) return;
    announcement.textContent = '';
    window.setTimeout(() => { announcement.textContent = message; }, 20);
  };
  const transition = (next) => {
    if (state.phase === next) return true;
    if (!phases[state.phase]?.has(next)) return false;
    state.phase = next;
    return true;
  };
  const setProgress = (stateText, title, message, jobText) => {
    if (progressState) progressState.textContent = stateText;
    if (progressTitle) progressTitle.textContent = title;
    if (progressMessage) progressMessage.textContent = message;
    if (progressJob) progressJob.textContent = jobText;
  };
  const setLoading = (loading) => {
    if (submitButton) {
      submitButton.disabled = loading;
      submitButton.classList.toggle('is-loading', loading);
      submitButton.setAttribute('aria-busy', String(loading));
    }
    if (cancelButton) cancelButton.disabled = !loading;
    if (buttonLabel) buttonLabel.textContent = loading ? 'Analyzing…' : 'Analyze incident';
    exampleButtons.forEach((button) => { button.disabled = loading; });
    show(progressPanel, loading);
    if (progressPanel) progressPanel.setAttribute('aria-busy', String(loading));
  };
  const setValidation = (message = '') => {
    if (!validationMessage) return;
    validationMessage.textContent = message;
    show(validationMessage, Boolean(message));
  };

  const updateInputState = () => {
    if (!input) return;
    const length = input.value.length;
    if (characterCount) {
      characterCount.textContent = `${length.toLocaleString()} / 4,000`;
      characterCount.classList.toggle('is-near-limit', length >= 3600);
      characterCount.classList.toggle('is-at-limit', length >= 4000);
    }
    if (length <= 4000) input.setCustomValidity('');
    if (length > 0) setValidation('');
  };

  const addRow = (list, left, right = '') => {
    if (!list) return;
    const item = document.createElement('li');
    const first = document.createElement('span');
    first.textContent = stringValue(left, 'Unknown');
    item.appendChild(first);
    if (right !== '') {
      const second = document.createElement('span');
      second.textContent = stringValue(right, 'Unknown');
      item.appendChild(second);
    }
    list.appendChild(item);
  };
  const addEvidence = (list, item) => {
    if (!list) return;
    const entry = document.createElement('li');
    const title = document.createElement('strong');
    const excerpt = document.createElement('span');
    title.textContent = stringValue(item?.title, 'Source');
    excerpt.textContent = stringValue(item?.excerpt, 'No excerpt supplied.');
    entry.append(title, excerpt);
    list.appendChild(entry);
  };
  const finiteConfidence = (value) => Math.min(1, Math.max(0, finiteNumber(value, 0)));

  const renderResult = (payload) => {
    const analysis = payload?.analysis;
    if (!analysis || typeof analysis !== 'object') throw new Error('The analysis response was incomplete.');
    const details = payload?.details && typeof payload.details === 'object' ? payload.details : {};
    const title = stringValue(analysis.title, 'Incident analysis');
    const severityValue = stringValue(analysis.severity, 'unknown').toLowerCase();
    const severity = ['low', 'medium', 'high', 'critical'].includes(severityValue) ? severityValue : 'unknown';
    const causes = Array.isArray(analysis.likely_causes) ? analysis.likely_causes.filter((item) => typeof item === 'string' && item.trim()).slice(0, 5) : [];
    const actions = Array.isArray(analysis.recommended_actions) ? analysis.recommended_actions.filter((item) => item && typeof item === 'object').slice(0, 5) : [];
    const evidence = Array.isArray(analysis.evidence) ? analysis.evidence.filter((item) => item && typeof item === 'object').slice(0, 8) : [];
    const confidence = finiteConfidence(analysis.confidence);
    const percent = Math.round(confidence * 100);
    if (resultTitle) resultTitle.textContent = title;
    const badge = $('#severity-badge');
    if (badge) { badge.textContent = severity; badge.className = `severity-badge ${severity}`; }
    const causeText = $('#cause-text');
    if (causeText) causeText.textContent = causes[0] || 'No likely cause was isolated.';
    const actionText = $('#action-text');
    if (actionText) actionText.textContent = stringValue(actions[0]?.text, 'Collect more evidence and review the incident with an operator.');
    const causesList = $('#causes-list');
    clear(causesList);
    (causes.length ? causes : ['No additional cause signal was reported.']).forEach((cause) => addRow(causesList, cause));
    const actionsList = $('#actions-list');
    clear(actionsList);
    (actions.length ? actions : [{ text: 'Review the evidence with an operator before changing a live system.' }]).forEach((action) => addRow(actionsList, stringValue(action.text, 'Review with an operator.')));
    const confidenceValue = $('#confidence-value');
    if (confidenceValue) confidenceValue.textContent = `${percent}%`;
    const confidenceBar = $('#confidence-bar');
    if (confidenceBar) confidenceBar.style.width = `${percent}%`;
    const confidenceDetail = $('#confidence-detail');
    if (confidenceDetail) confidenceDetail.textContent = Number.isFinite(Number(analysis.confidence)) ? `Exact score: ${confidence.toFixed(3)}` : 'Score not reported';
    const knowledge = details.knowledge && typeof details.knowledge === 'object' ? details.knowledge : {};
    const runbooks = Math.max(0, Math.trunc(finiteNumber(knowledge.runbooks_retrieved, 0)));
    const history = Math.max(0, Math.trunc(finiteNumber(knowledge.historical_incidents, 0)));
    const knowledgeSummary = $('#knowledge-summary');
    if (knowledgeSummary) knowledgeSummary.textContent = `${runbooks} runbook source${runbooks === 1 ? '' : 's'} retrieved · ${history} historical match${history === 1 ? '' : 'es'}`;
    const evidenceCount = $('#evidence-count');
    if (evidenceCount) evidenceCount.textContent = `${evidence.length} relevant source${evidence.length === 1 ? '' : 's'}`;
    const limitations = Array.isArray(analysis.limitations) ? analysis.limitations.filter((item) => typeof item === 'string' && item.trim()).slice(0, 3) : [];
    const limitationText = $('#limitation-text');
    if (limitationText) limitationText.textContent = limitations.length ? limitations.join(' · ') : 'No additional limitation reported.';
    const posture = $('#review-posture');
    const degraded = Boolean(details.degraded) || Boolean(details.remediation?.status && details.remediation.status !== 'disabled');
    if (posture) posture.textContent = degraded ? 'Review required' : 'Evidence-aware';

    const stepsList = $('#steps-list');
    clear(stepsList);
    (Array.isArray(details.steps) ? details.steps : []).slice(0, 8).forEach((step) => addRow(stepsList, step?.name, step?.status));
    if (stepsList && !stepsList.children.length) addRow(stepsList, 'No step status reported.');
    const evidenceList = $('#evidence-list');
    clear(evidenceList);
    evidence.forEach((item) => addEvidence(evidenceList, item));
    if (evidenceList && !evidenceList.children.length) addRow(evidenceList, 'No evidence was selected.');
    const toolsList = $('#tools-list');
    clear(toolsList);
    (Array.isArray(details.tools) ? details.tools : []).slice(0, 8).forEach((item) => addRow(toolsList, item?.name, item?.status));
    if (toolsList && !toolsList.children.length) addRow(toolsList, 'No tool status reported.');
    const models = details.models && typeof details.models === 'object' ? details.models : {};
    const modelList = $('#model-list');
    clear(modelList);
    addRow(modelList, 'Classifier', models.classifier);
    addRow(modelList, 'Resolution', models.resolution);
    (Array.isArray(details.safety) ? details.safety : []).slice(0, 4).forEach((item) => addRow(modelList, item?.name, item?.status));
    const remediation = details.remediation && typeof details.remediation === 'object' ? details.remediation : {};
    const remediationList = $('#remediation-list');
    clear(remediationList);
    addRow(remediationList, 'Status', remediation.status || 'disabled');
    addRow(remediationList, 'Action', remediation.action || 'none');
    addRow(remediationList, 'Health', remediation.health_verified ? 'verified' : 'not verified');
    const confidenceBlock = $('.confidence-block');
    if (confidenceBlock) confidenceBlock.setAttribute('aria-label', `Confidence ${percent} percent; exact score ${confidence.toFixed(3)}`);
    show(errorPanel, false);
    show(resultPanel, true);
    announce(`Analysis complete. ${title}. Severity ${severity}. Confidence ${percent} percent.`);
    if (resultTitle && typeof resultTitle.focus === 'function') {
      const bounds = resultTitle.getBoundingClientRect();
      const outsideViewport = bounds.top < 0 || bounds.bottom > window.innerHeight;
      if (outsideViewport && typeof resultTitle.scrollIntoView === 'function') resultTitle.scrollIntoView({ block: 'start' });
      resultTitle.focus({ preventScroll: true });
    }
  };

  const eventStatus = (event) => ['queued', 'running', 'complete', 'failed', 'cancelled'].includes(event?.status) ? event.status : null;
  const applyEvent = (event, requestId) => {
    if (state.activeRequestId !== requestId) return null;
    const status = eventStatus(event);
    if (!status) throw new Error('Live progress returned an invalid state.');
    if (status === 'queued') {
      if (!['starting', 'queued'].includes(state.phase)) return null;
      transition('queued');
      const position = Number.isInteger(event.position) && event.position > 0 ? event.position : null;
      const queueMessage = position === 1 ? "Another investigation is running. You're next in queue." : position ? `Your investigation is in the queue. Position ${position}.` : 'Your investigation is waiting for capacity.';
      setProgress('Queued', 'Your investigation is waiting for capacity.', queueMessage, 'The request will start automatically when the active review finishes.');
    } else if (status === 'running') {
      if (!['starting', 'queued', 'running'].includes(state.phase)) return null;
      transition('running');
      setProgress('Running', 'OpsPilot is investigating the incident.', 'Checking the incident details and relevant evidence.', 'The review is bounded and read-only until a safety gate is passed.');
    } else if (status === 'complete') {
      if (!['starting', 'queued', 'running'].includes(state.phase)) return null;
      transition('success');
      return event.result || null;
    } else if (status === 'failed') {
      transition('error');
      const failure = event.error && typeof event.error === 'object' ? event.error : {};
      const error = new Error(stringValue(failure.message, 'OpsPilot could not complete the investigation. Try again.'));
      error.retryable = failure.retryable !== false;
      throw error;
    } else if (status === 'cancelled') {
      transition('error');
      const error = new Error('The investigation was cancelled.');
      error.retryable = false;
      throw error;
    }
    return null;
  };

  const readEvents = async (response, requestId) => {
    if (!response.body) throw new Error('Live progress is unavailable. Please try again.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let result = null;
    const consume = (line) => {
      const clean = line.trim();
      if (!clean) return;
      let event;
      try { event = JSON.parse(clean); } catch (_) { throw new Error('Live progress returned malformed data.'); }
      const value = applyEvent(event, requestId);
      if (value) result = value;
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
  };

  const wait = (duration) => new Promise((resolve) => window.setTimeout(resolve, duration));
  const statusOnce = async (url, requestId) => {
    const response = await fetch(url, { signal: state.controller?.signal });
    const payload = await response.json().catch(() => ({}));
    if (state.activeRequestId !== requestId) return null;
    if (!response.ok) throw new Error('The investigation status is unavailable. Please try again.');
    return payload;
  };
  const consumeJob = async (job, requestId) => {
    let attempts = 0;
    while (state.activeRequestId === requestId) {
      try {
        const response = await fetch(job.events_url, { signal: state.controller?.signal });
        if (!response.ok) throw new Error('stream');
        const result = await readEvents(response, requestId);
        if (result) return result;
        const status = await statusOnce(job.status_url, requestId);
        if (status?.result) return status.result;
        if (['complete', 'failed', 'cancelled'].includes(status?.status)) return null;
        throw new Error('stream-ended');
      } catch (error) {
        if (error.name === 'AbortError' || state.cancelled || state.activeRequestId !== requestId) throw error;
        if (attempts >= 2) throw new Error('Live progress was interrupted. Please try again.');
        attempts += 1;
        const status = await statusOnce(job.status_url, requestId);
        if (status?.result) return status.result;
        if (['failed', 'cancelled'].includes(status?.status)) return null;
        await wait(250 * (2 ** (attempts - 1)));
      }
    }
    return null;
  };

  const errorCopy = (error) => {
    if (state.cancelled) return { message: 'The investigation was cancelled.', retryable: false };
    if (state.timedOut) return { message: 'The investigation took too long. Try again in a moment.', retryable: true };
    if (error?.name === 'TypeError') return { message: 'OpsPilot could not reach the analysis service. Check your connection and try again.', retryable: true };
    return { message: stringValue(error?.message, 'OpsPilot could not complete this investigation. Try again.'), retryable: error?.retryable !== false };
  };

  const runAnalysis = async (description) => {
    const requestId = window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    state.activeRequestId = requestId;
    state.clientRequestId = requestId;
    state.controller = new AbortController();
    state.jobId = null;
    state.lastDescription = description;
    state.cancelled = false;
    state.timedOut = false;
    transition('starting');
    show(errorPanel, false);
    show(resultPanel, false);
    show(retryButton, false);
    setProgress('Starting', 'OpsPilot is preparing the review.', 'Connecting to the analysis service.', 'A bounded analysis slot is reserved for this request.');
    setLoading(true);
    state.timeoutId = window.setTimeout(() => {
      if (state.activeRequestId !== requestId) return;
      state.timedOut = true;
      state.controller?.abort();
    }, 125000);
    try {
      const response = await fetch('/api/incidents/analyze/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description, client_request_id: requestId }),
        signal: state.controller.signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (state.activeRequestId !== requestId) return;
      if (!response.ok) {
        const apiError = payload?.error && typeof payload.error === 'object' ? payload.error : {};
        const error = new Error(stringValue(apiError.message, response.status === 429 ? 'The public demo is busy. Please try again shortly.' : 'OpsPilot could not complete this investigation. Try again.'));
        error.retryable = apiError.retryable !== false;
        throw error;
      }
      state.jobId = stringValue(payload.job_id);
      if (!state.jobId || !payload.events_url || !payload.status_url) throw new Error('The analysis service returned an incomplete job.');
      const result = await consumeJob(payload, requestId);
      if (result) renderResult(result);
      else if (state.phase !== 'error') throw new Error('The analysis service returned no result.');
    } catch (error) {
      if (error?.name === 'AbortError' && !state.cancelled && !state.timedOut) return;
      if (state.activeRequestId !== requestId) return;
      const copy = errorCopy(error);
      transition('error');
      if (errorMessage) errorMessage.textContent = copy.message;
      show(retryButton, copy.retryable);
      show(errorPanel, true);
      announce(copy.message);
    } finally {
      if (state.activeRequestId === requestId) {
        window.clearTimeout(state.timeoutId);
        state.timeoutId = null;
        state.activeRequestId = null;
        state.controller = null;
        setLoading(false);
        if (state.phase === 'starting' || state.phase === 'queued' || state.phase === 'running') transition('idle');
      }
    }
  };

  const newAnalysis = () => {
    if (state.activeRequestId) return;
    show(resultPanel, false);
    show(errorPanel, false);
    show(retryButton, false);
    setValidation('');
    if (input) { input.value = ''; input.setCustomValidity(''); updateInputState(); input.focus(); }
    transition('idle');
  };

  exampleButtons.forEach((button) => button.addEventListener('click', () => {
    if (!input || state.activeRequestId) return;
    input.value = button.dataset.example || '';
    updateInputState();
    input.focus();
  }));
  input?.addEventListener('input', updateInputState);
  form?.addEventListener('submit', (event) => {
    event.preventDefault();
    if (state.activeRequestId || !input) return;
    const description = input.value.trim();
    if (!description) {
      const message = 'Describe the incident in at least one character.';
      input.setCustomValidity(message);
      setValidation(message);
      input.reportValidity();
      return;
    }
    if (description.length > 4000) {
      const message = 'Keep the incident under 4,000 characters.';
      input.setCustomValidity(message);
      setValidation(message);
      input.reportValidity();
      return;
    }
    input.setCustomValidity('');
    runAnalysis(description);
  });
  retryButton?.addEventListener('click', () => { if (!state.activeRequestId && state.lastDescription) runAnalysis(state.lastDescription); });
  $('#new-analysis')?.addEventListener('click', newAnalysis);
  $('#new-analysis-error')?.addEventListener('click', newAnalysis);
  cancelButton?.addEventListener('click', async () => {
    if (!state.jobId || !state.activeRequestId) return;
    state.cancelled = true;
    cancelButton.disabled = true;
    try { await fetch(`/api/incidents/analyze/jobs/${encodeURIComponent(state.jobId)}`, { method: 'DELETE' }); } catch (_) { /* local cancellation still applies */ }
    state.controller?.abort();
  });
  updateInputState();
}());
