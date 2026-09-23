/* Backend settings stay separate from verification/report rendering. */
(() => {
  const form = document.querySelector('#config-form');
  const button = form.querySelector('button[type="submit"]');
  const status = document.querySelector('#config-status');
  const names = ['mock', 'exa', 'firecrawl'];
  let loaded = false;
  let loading = false;
  const field = id => document.getElementById(id);
  const message = (text, error = false) => {
    status.textContent = text;
    status.className = `save-status ${error ? 'error' : 'success'}`;
  };
  function summarize(config) {
    field('backend-status').textContent = config.search_providers.map(name => ({mock:'Mock',exa:'Exa',firecrawl:'Firecrawl'}[name] || name)).join(' + ');
    field('backend-detail').textContent = config.search_providers.includes('mock') ? '演示数据已启用' : '已选择搜索后端';
    document.querySelector('.status-dot').classList.add('ready');
    field('claim-analyzer').value = config.claim_analyzer || 'llm';
    field('claim-analyzer-state').textContent = ({rules:'规则解析', llm:'兼容接口模型'})[config.claim_analyzer] || config.claim_analyzer;
    field('evidence-analyzer').value = config.evidence_analyzer || 'local';
    field('evidence-analyzer-state').textContent = ({local:'本地训练模型', rules:'规则判断', llm:'兼容接口模型'})[config.evidence_analyzer] || config.evidence_analyzer;
    for (const name of ['exa','firecrawl']) {
      field(`${name}-state`).textContent = config[`${name}_configured`] ? `已保存 · ${config[`${name}_api_key`]}` : '未配置';
    }
    field('llm-url').value = config.llm_api_url;
    field('llm-model').value = config.llm_model;
    field('llm-state').textContent = config.llm_configured ? `已配置 · ${config.llm_model} · ${config.llm_api_key}` : '未配置';
    field('backup-llm-url').value = config.backup_llm_api_url;
    field('backup-llm-model').value = config.backup_llm_model;
    field('backup-llm-state').textContent = config.backup_llm_configured ? `备用模型 · ${config.backup_llm_model} · ${config.backup_llm_api_key}` : '未配置';
  }
  async function request(options) {
    const response = await fetch('/api/v1/config', options);
    const data = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('；') : data.detail;
      throw new Error(detail || '配置请求失败，请重试。');
    }
    return data;
  }
  async function load() {
    if (loaded || loading) return;
    loading = true; button.disabled = true;
    try {
      const config = await request();
      for (const name of names) field(`use-${name}`).checked = config.search_providers.includes(name);
      field('content-provider').value = config.content_provider;
      field('max-results').value = config.max_search_results;
      field('max-evidence').value = config.max_evidence_items;
      field('timeout').value = config.request_timeout_seconds;
      for (const name of ['exa','firecrawl']) field(`${name}-url`).value = config[`${name}_api_url`];
      summarize(config); loaded = true; status.textContent = '';
    } catch (error) {
      message(`读取配置失败：${error.message}。重新进入本页可重试。`, true);
      field('backend-status').textContent = '配置暂不可用';
    } finally { loading = false; button.disabled = !loaded; }
  }
  document.querySelector('[data-view="sources"]').addEventListener('click', load);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const providers = names.filter(name => field(`use-${name}`).checked);
    if (!providers.length) { message('请至少启用一个搜索后端。', true); return; }
    const body = {
      search_providers: providers, content_provider: field('content-provider').value,
      max_search_results: Number(field('max-results').value),
      max_evidence_items: Number(field('max-evidence').value),
      request_timeout_seconds: Number(field('timeout').value),
      claim_analyzer: field('claim-analyzer').value,
      evidence_analyzer: field('evidence-analyzer').value,
      llm_api_url: field('llm-url').value.trim(),
      llm_model: field('llm-model').value.trim(),
      llm_api_key: field('llm-key').value.trim() || null,
      backup_llm_api_url: field('backup-llm-url').value.trim(),
      backup_llm_model: field('backup-llm-model').value.trim(),
      backup_llm_api_key: field('backup-llm-key').value.trim() || null,
    };
    for (const name of ['exa','firecrawl']) {
      body[`${name}_api_url`] = field(`${name}-url`).value.trim();
      body[`${name}_api_key`] = field(`${name}-key`).value.trim() || null;
    }
    button.disabled = true; message('正在保存…');
    try {
      const config = await request({method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      for (const name of ['exa','firecrawl']) field(`${name}-key`).value = '';
      field('llm-key').value = ''; field('backup-llm-key').value = '';
      summarize(config); message('配置已保存，下次核验立即生效。');
    } catch (error) { message(error.message, true); }
    finally { button.disabled = false; }
  });
  load();
})();
