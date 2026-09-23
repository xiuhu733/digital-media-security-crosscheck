/* Completed reports live in SQLite; browser memory is not the source of truth. */
let historyRequest = 0;
async function historyJson(url) {
  const response = await fetch(url, {cache: 'no-store'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || '读取核验历史失败，请稍后重试。');
  return data;
}

async function renderHistory(offset = 0, notice = '') {
  const request = ++historyRequest;
  const panel = document.querySelector('.history-panel');
  panel.setAttribute('aria-busy', 'true');
  panel.innerHTML = '<p role="status">正在加载核验历史…</p>';
  try {
    const page = await historyJson(`/api/v1/history?limit=20&offset=${offset}`);
    if (request !== historyRequest) return;
    if (!page.items.length && page.total && offset) return renderHistory(Math.floor((page.total - 1) / page.limit) * page.limit, notice);
    if (!page.items.length) {
      panel.innerHTML = '<div class="empty-icon">◷</div><h3>暂无核验记录</h3><p>完成核验后，记录会自动保存在本机，供日后查看。</p>';
      const message = document.createElement('p');
      message.setAttribute('role', 'status');
      message.textContent = notice;
      panel.appendChild(message);
      return;
    }
    panel.innerHTML = `<div class="card-kicker">核验历史 · 共 ${page.total} 条</div>
      <p id="history-message" class="inline-message" role="status"></p>
      <div class="evidence-list">${page.items.map(item => `<div class="source-card history-entry"><button type="button" class="history-row" data-report="${escapeHtml(item.request_id)}">
        <div class="source-logo">核</div><div><b>${escapeHtml(item.claim_text)}</b>
        <p>${new Date(item.created_at).toLocaleString('zh-CN', {hour12:false})} · ${escapeHtml(relationLabel[item.conclusion] || item.conclusion)} · ${item.evidence_count} 条证据</p></div>
      </button><button type="button" class="history-delete" data-delete="${escapeHtml(item.request_id)}" aria-label="删除核验记录：${escapeHtml(item.claim_text)}">删除</button></div>`).join('')}</div>
      <div class="history-pagination"><button type="button" id="history-prev" ${offset === 0 ? 'disabled' : ''}>上一页</button>
      <span>第 ${Math.floor(offset / page.limit) + 1} / ${Math.ceil(page.total / page.limit)} 页</span>
      <button type="button" id="history-next" ${offset + page.limit >= page.total ? 'disabled' : ''}>下一页</button></div>`;
    panel.querySelector('#history-message').textContent = notice;
    panel.querySelector('#history-prev').addEventListener('click', () => renderHistory(Math.max(0, offset - page.limit)));
    panel.querySelector('#history-next').addEventListener('click', () => renderHistory(offset + page.limit));
    panel.querySelectorAll('[data-delete]').forEach(button => button.addEventListener('click', async () => {
      const id = button.dataset.delete;
      const item = page.items.find(item => item.request_id === id);
      if (!window.confirm(`确认删除这条核验记录？\n\n${item.claim_text}\n\n删除后无法恢复。`)) return;
      const message = panel.querySelector('#history-message');
      const controls = [...panel.querySelectorAll('button')].map(control => [control, control.disabled]);
      controls.forEach(([control]) => { control.disabled = true; });
      message.textContent = '正在删除记录…';
      try {
        const response = await fetch(`/api/v1/history/${encodeURIComponent(id)}`, {method: 'DELETE'});
        if (!response.ok && response.status !== 404) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.detail || `删除核验记录失败（HTTP ${response.status}），请重试。`);
        }
        if (resultSection.dataset.reportId === id) {
          delete resultSection.dataset.reportId;
          resultGrid.replaceChildren();
          resultSection.classList.add('hidden');
          emptyState.classList.remove('hidden');
          document.querySelector('#report-id').textContent = '—';
          document.querySelector('#report-time').textContent = '—';
        }
        if (request === historyRequest) await renderHistory(offset, response.status === 404 ? '这条记录已不存在，历史已更新。' : '核验记录已删除。');
      } catch (error) {
        if (request === historyRequest) message.textContent = error.message || '删除核验记录失败，请重试。';
      } finally {
        controls.forEach(([control, disabled]) => { control.disabled = disabled; });
      }
    }));
    panel.querySelectorAll('[data-report]').forEach(button => button.addEventListener('click', async () => {
      button.disabled = true;
      const message = panel.querySelector('#history-message');
      message.textContent = '正在打开核验报告…';
      try {
        const report = await historyJson(`/api/v1/history/${encodeURIComponent(button.dataset.report)}`);
        // Discard a response from a history page the user has already replaced.
        if (request !== historyRequest) return;
        document.querySelector('[data-view="verify"]').click();
        renderReport(report);
        message.textContent = '';
      } catch (error) { message.textContent = error.message; }
      finally { button.disabled = false; }
    }));
  } catch (error) {
    if (request !== historyRequest) return;
    panel.innerHTML = '<p class="inline-message" role="status"></p><button type="button" class="primary-btn">重新加载</button>';
    panel.querySelector('p').textContent = error.message;
    panel.querySelector('button').addEventListener('click', () => renderHistory(offset));
  } finally {
    if (request === historyRequest) panel.setAttribute('aria-busy', 'false');
  }
}
