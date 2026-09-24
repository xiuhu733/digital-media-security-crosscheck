const form = document.querySelector('#verify-form');
const claimInput = document.querySelector('#claim');
const count = document.querySelector('#char-count');
const resultSection = document.querySelector('#result-section');
const emptyState = document.querySelector('#empty-state');
const resultGrid = document.querySelector('#result-grid');
const submitBtn = document.querySelector('#submit-btn');
const progressPanel = document.querySelector('#progress-panel');
const progressMessage = document.querySelector('#progress-message');
const progressPercent = document.querySelector('#progress-percent');
const progressBar = document.querySelector('#progress-bar');
const progressLog = document.querySelector('#progress-log');



claimInput.addEventListener('input', () => { count.textContent = `${claimInput.value.length} / 2000`; });
document.querySelectorAll('[data-suggestion]').forEach(button => button.addEventListener('click', () => { claimInput.value = button.dataset.suggestion; claimInput.dispatchEvent(new Event('input')); claimInput.focus(); }));

document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
  document.querySelectorAll('.view').forEach(view => view.classList.remove('active-view'));
  button.classList.add('active'); document.querySelector(`#${button.dataset.view}-view`).classList.add('active-view'); if (button.dataset.view === 'history') renderHistory(); document.querySelector('#page-crumb').textContent = button.textContent.trim();
}));

const relationLabel = {refutes:'存在反驳', supports:'获得支持', conflicts:'证据冲突', insufficient:'证据不足'};
function analysisMethodLabel(method) {
  if (method?.startsWith('hybrid-local-')) return method.endsWith('backup') ? '本地模型 + 备用大模型' : '本地模型 + 大模型';
  return ({'llm-primary': '主模型', 'llm-backup': '备用模型', llm: '大模型', 'local-relation-model': '本地训练模型', rules: '规则'})[method] || '未知';
}
function modelSucceeded(method) {
  return ['llm', 'llm-primary', 'llm-backup', 'local-relation-model'].includes(method) || method?.startsWith('hybrid-local-');
}
function visibleAnalysisWarnings(report) {
  // Older SQLite snapshots include failed attempts even after a successful fallback.
  return (report.analysis_warnings || []).filter(warning => {
    if (modelSucceeded(report.claim_analysis_method) && /^llm(?:-primary|-backup)?解析失败：/.test(warning)) return false;
    if (modelSucceeded(report.evidence_analysis_method) && /^llm(?:-primary|-backup)?证据判断失败：/.test(warning)) return false;
    return true;
  });
}
function renderReport(report) {
  resultSection.dataset.reportId = report.request_id;
  const badgeClass = report.conclusion === 'refutes' ? 'refuted' : report.conclusion;
  const evidence = report.evidence.map(item => `<article class="evidence-item"><div class="evidence-id">${item.id}</div><div><div class="evidence-title">${escapeHtml(item.title || '未命名来源')}</div><div class="evidence-excerpt">${escapeHtml(item.excerpt)}</div><div class="analysis-reason">${escapeHtml(item.analysis_reason || '')}</div><div class="evidence-link">内容来源：${escapeHtml((item.content_sources || [item.provider]).join(' + '))}</div><a class="evidence-link" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.url)}</a></div><span class="relation ${item.relation}">${relationLabel[item.relation] || item.relation}</span></article>`).join('');
  const warnings = visibleAnalysisWarnings(report);
  const analysisNotice = warnings.length ? `<p class="analysis-reason">${escapeHtml(warnings.join('；'))}</p>` : '';
  resultGrid.innerHTML = `<article class="result-card conclusion-card"><div class="card-kicker">核验结论</div><div class="conclusion-badge ${badgeClass}"><span>●</span>${relationLabel[report.conclusion] || report.conclusion}</div><p class="conclusion-summary">${escapeHtml(report.summary)}</p><p class="claim-quote">${escapeHtml(report.claim.text)}</p></article><article class="result-card"><div class="card-kicker">主张要素 · ${modelSucceeded(report.claim_analysis_method) ? '解析成功 · ' : ''}${analysisMethodLabel(report.claim_analysis_method)}</div><div class="claim-quote">主体：${escapeHtml(report.claim.subject || '未识别')}<br>对象：${escapeHtml(report.claim.object || '未识别')}<br>行为：${escapeHtml(report.claim.action || '未识别')}<br>事件：${escapeHtml(report.claim.event || '未识别')}<br>时间：${escapeHtml(report.claim.time || '未识别')}<br>天数：${report.claim.duration_days ? `${report.claim.duration_days} 天` : '未识别'}<br>地点：${escapeHtml(report.claim.location || '未识别')}<br>范围：${escapeHtml(report.claim.scope || '未识别')}</div>${analysisNotice}<div class="card-kicker" style="margin-top:23px">使用的搜索服务</div><p class="conclusion-summary" style="margin-top:10px">${escapeHtml(report.searched_providers.join(' · '))}</p></article><article class="result-card evidence-card"><div class="card-kicker">证据判断 · ${report.evidence.length} 条 · ${modelSucceeded(report.evidence_analysis_method) ? '判断完成 · ' : ''}${analysisMethodLabel(report.evidence_analysis_method)}</div><h3>证据与来源</h3><div class="evidence-list">${evidence}</div></article>`;
  document.querySelector('#report-id').textContent = `REPORT ${report.request_id.slice(0, 8).toUpperCase()}`;
  document.querySelector('#report-id').title = `诊断请求 ID：${report.request_id}`;
  document.querySelector('#report-time').textContent = new Date(report.created_at).toLocaleString('zh-CN', {hour12:false});
  resultSection.classList.remove('hidden'); emptyState.classList.add('hidden'); resultSection.scrollIntoView({behavior:'smooth', block:'start'});
}
function escapeHtml(value) { const div = document.createElement('div'); div.textContent = value || ''; return div.innerHTML.replaceAll('"', '&quot;').replaceAll("'", '&#39;'); }
form.addEventListener('submit', async event => {
  event.preventDefault(); const claim = claimInput.value.trim(); if (claim.length < 5) { claimInput.focus(); claimInput.classList.add('shake'); setTimeout(() => claimInput.classList.remove('shake'), 350); return; }
  submitBtn.disabled = true; document.querySelector('#verify-message').textContent = ''; submitBtn.classList.add('loading'); submitBtn.querySelector('.btn-label').textContent = '核验进行中…'; showProgress();
  let diagnosticRequestId = '';
  try {
    const response = await fetch('/api/v1/verifications/stream', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({claim})});
    diagnosticRequestId = response.headers.get('x-request-id') || '';
    if (!response.ok || !response.body) throw new Error(`核验接口返回 HTTP ${response.status}`);
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let receivedResult = false;
    while (true) {
      const {value, done} = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
      const frames = buffer.split('\n\n'); buffer = frames.pop() || '';
      for (const frame of frames) {
        const line = frame.split('\n').find(item => item.startsWith('data: ')); if (!line) continue;
        const eventData = JSON.parse(line.slice(6));
        if (eventData.type === 'progress') updateProgress(eventData);
        if (eventData.type === 'result') { renderReport(eventData.report); finishProgress(); receivedResult = true; }
        if (eventData.type === 'error') throw new Error(eventData.message || '核验失败');
      }
      if (done) break;
    }
    if (!receivedResult) throw new Error('核验连接提前结束，未收到完整报告，请稍后重试。');
  }
  catch (error) { document.querySelector('#verify-message').textContent = error.message + (diagnosticRequestId ? `（诊断 ID：${diagnosticRequestId}）` : ''); }
  finally { submitBtn.disabled = false; submitBtn.classList.remove('loading'); submitBtn.querySelector('.btn-label').textContent = '开始交叉核验'; }
});

function showProgress() { progressPanel.classList.remove('hidden'); progressMessage.textContent = '等待开始…'; progressPercent.textContent = '0%'; progressBar.style.width = '0%'; progressLog.innerHTML = ''; }
function updateProgress(event) { progressMessage.textContent = event.message; progressPercent.textContent = `${event.percent}%`; progressBar.style.width = `${event.percent}%`; const line = document.createElement('div'); line.textContent = `${new Date().toLocaleTimeString('zh-CN', {hour12:false})}  ${event.message}`; progressLog.appendChild(line); progressLog.scrollTop = progressLog.scrollHeight; }
function finishProgress() { progressMessage.textContent = '核验完成，记录已保存'; progressPercent.textContent = '100%'; progressBar.style.width = '100%'; }
