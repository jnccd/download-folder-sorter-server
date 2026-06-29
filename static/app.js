const state = {
  config: null,
};

async function loadConfig() {
  const res = await fetch('/api/config');
  state.config = await res.json();
  document.getElementById('downloadFolder').value = state.config.download_folder || '';
  document.getElementById('sortDelay').value = state.config.sort_delay_seconds || 3;
  renderRules();
  void updateStatus();
}

function renderRules() {
  const container = document.getElementById('rules');
  container.innerHTML = '';
  const rules = state.config.rules || [];
  rules.forEach((rule, index) => {
    const row = document.createElement('div');
    row.className = 'rule';
    row.innerHTML = `
      <label>Name<input data-field="name" data-index="${index}" value="${escapeHtml(rule.name || '')}" /></label>
      <label>Match<input data-field="match" data-index="${index}" value="${escapeHtml(rule.match || '')}" /></label>
      <label>Target<input data-field="target" data-index="${index}" value="${escapeHtml(rule.target || '')}" /></label>
      <button class="secondary" data-remove="${index}">Remove</button>
    `;
    container.appendChild(row);
  });
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function saveConfig() {
  const payload = {
    download_folder: document.getElementById('downloadFolder').value,
    sort_delay_seconds: Number(document.getElementById('sortDelay').value || 3),
    rules: [],
  };

  const ruleRows = document.querySelectorAll('#rules .rule');
  ruleRows.forEach((row) => {
    const inputs = row.querySelectorAll('input');
    payload.rules.push({
      name: inputs[0].value,
      match: inputs[1].value,
      target: inputs[2].value,
    });
  });

  const res = await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  state.config = await res.json();
  renderRules();
  updateStatus();
}

async function sortNow() {
  await fetch('/api/sort', { method: 'POST' });
  updateStatus();
}

async function updateStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    document.getElementById('statusState').textContent = data.status || 'idle';
    document.getElementById('statusFolder').textContent = data.download_folder || '—';
    document.getElementById('statusRules').textContent = data.rules ?? 0;
    document.getElementById('statusMessage').textContent = data.message || '—';

    const eventList = document.getElementById('statusEvents');
    eventList.innerHTML = '';
    const events = Array.isArray(data.recent_events) ? data.recent_events : [];
    if (events.length === 0) {
      eventList.innerHTML = '<li>No recent activity yet.</li>';
    } else {
      events.slice().reverse().forEach((event) => {
        const item = document.createElement('li');
        item.textContent = event;
        eventList.appendChild(item);
      });
    }
  } catch (error) {
    document.getElementById('statusMessage').textContent = 'Status unavailable';
  }
}

document.getElementById('saveConfig').addEventListener('click', saveConfig);
document.getElementById('sortNow').addEventListener('click', sortNow);
document.getElementById('addRule').addEventListener('click', () => {
  state.config.rules.push({ name: '', match: '', target: '' });
  renderRules();
});
document.getElementById('rules').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-remove]');
  if (button) {
    const index = Number(button.getAttribute('data-remove'));
    state.config.rules.splice(index, 1);
    renderRules();
  }
});

loadConfig();
setInterval(updateStatus, 1500);
