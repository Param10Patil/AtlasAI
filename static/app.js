const form = document.querySelector('#incident-form');
const input = document.querySelector('#incident-description');
const submitButton = document.querySelector('#submit-button');
const progressPanel = document.querySelector('#progress-panel');
const errorPanel = document.querySelector('#error-panel');
const resultPanel = document.querySelector('#result-panel');
const errorMessage = document.querySelector('#error-message');
const state = { activeRequestId: null, controller: null, lastDescription: '' };

const show = (element, visible) => { if (element) element.hidden = !visible; };
const escapeHtml = (value) => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/'/g, '&#39;').replace(new RegExp(String.fromCharCode(34), 'g'), '&quot;');
const setLoading = (loading) => { submitButton.disabled = loading; submitButton.querySelector('span').textContent = loading ? 'Investigating' : 'Analyze incident'; show(progressPanel, loading); if (progressPanel) progressPanel.setAttribute('aria-busy', String(loading)); };

document.querySelectorAll('[data-example]').forEach((button) => button.addEventListener('click', () => { input.value = button.dataset.example || ''; input.focus(); }));

const renderResult = (payload) => {
  const analysis = payload.analysis || {};
  const details = payload.details || {};
  document.querySelector('#result-title').textContent = analysis.title || 'Incident analysis';
  const badge = document.querySelector('#severity-badge');
  badge.textContent = analysis.severity || 'unknown';
  badge.className = 'severity-badge ' + (analysis.severity || '');
  document.querySelector('#cause-text').textContent = (analysis.likely_causes || [])[0] || 'No likely cause was isolated.';
  const action = (analysis.recommended_actions || [])[0];
  document.querySelector('#action-text').textContent = action ? action.text : 'Collect more evidence and review the incident with an operator.';
  document.querySelector('#confidence-value').textContent = Math.round((Number(analysis.confidence) || 0) * 100) + '%';
  const evidence = analysis.evidence || [];
  document.querySelector('#evidence-count').textContent = evidence.length + ' relevant source' + (evidence.length === 1 ? '' : 's');
  document.querySelector('#limitation-text').textContent = (analysis.limitations || []).join(' · ');
  document.querySelector('#steps-list').innerHTML = (details.steps || []).map((step) => '<li><span>' + escapeHtml(step.name || 'step') + '</span><span>' + escapeHtml(step.status || 'unknown') + '</span></li>').join('');
  document.querySelector('#evidence-list').innerHTML = evidence.map((item) => '<li><strong>' + escapeHtml(item.title || 'Source') + '</strong><span>' + escapeHtml(item.excerpt || '') + '</span></li>').join('') || '<li><span>No evidence was selected.</span></li>';
  document.querySelector('#tools-list').innerHTML = (details.tools || []).map((tool) => '<li><span>' + escapeHtml(tool.name || 'tool') + '</span><span>' + escapeHtml(tool.status || 'unknown') + '</span></li>').join('') || '<li><span>No tool status reported</span></li>';
  const models = details.models || {};
  const safety = (details.safety || []).map((item) => '<li><span>' + escapeHtml(item.name || 'safety check') + '</span><span>' + escapeHtml(item.status || 'unknown') + '</span></li>').join('');
  document.querySelector('#model-list').innerHTML = '<li><span>Classifier</span><span>' + escapeHtml(models.classifier || 'fallback') + '</span></li><li><span>Resolution</span><span>' + escapeHtml(models.resolution || 'unknown') + '</span></li>' + safety;
  show(resultPanel, true);
};

const runAnalysis = async (description) => {
  const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + '-' + String(Math.random());
  state.activeRequestId = requestId;
  state.controller = new AbortController();
  state.lastDescription = description;
  show(errorPanel, false);
  show(resultPanel, false);
  setLoading(true);
  try {
    const response = await fetch('/api/incidents/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description }), signal: state.controller.signal });
    const payload = await response.json().catch(() => ({}));
    if (state.activeRequestId !== requestId) return;
    if (!response.ok) throw new Error(payload.error?.message || 'OpsPilot could not complete the investigation.');
    renderResult(payload);
  } catch (error) {
    if (error.name === 'AbortError' || state.activeRequestId !== requestId) return;
    errorMessage.textContent = error.message || 'Please try again.';
    show(errorPanel, true);
  } finally {
    if (state.activeRequestId === requestId) { state.activeRequestId = null; state.controller = null; setLoading(false); }
  }
};

form?.addEventListener('submit', (event) => {
  event.preventDefault();
  if (state.activeRequestId) return;
  const description = input.value.trim();
  if (!description || description.length > 4000) { input.setCustomValidity(description ? 'Keep the incident under 4,000 characters.' : 'Describe the incident before analyzing.'); input.reportValidity(); return; }
  input.setCustomValidity('');
  runAnalysis(description);
});
document.querySelector('#retry-button')?.addEventListener('click', () => runAnalysis(state.lastDescription || input.value.trim()));
document.querySelector('#new-analysis')?.addEventListener('click', () => { show(resultPanel, false); input.value = ''; input.focus(); });
