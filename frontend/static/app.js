'use strict';

const transcript = document.getElementById('transcript');
const welcome = document.getElementById('welcome');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const cancelBtn = document.getElementById('cancel');
const meta = document.getElementById('meta');

let sessionId = null;
let busy = false;
const toolCards = {};   // tool_use id -> { body, status }

/* ---------- dom helpers ---------- */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function add(node) {
  if (welcome && welcome.parentNode) welcome.remove();
  transcript.appendChild(node);
  transcript.scrollTop = transcript.scrollHeight;
  return node;
}

/* ---------- renderers ---------- */
function addUser(text) {
  const row = el('div', 'row user');
  row.appendChild(el('div', 'bubble', text));
  add(row);
}
function addAssistant(text) {
  const row = el('div', 'row assistant');
  row.appendChild(el('div', 'assistant-text', text));
  add(row);
}
function addNotice(text, kind) {
  add(el('div', 'notice ' + (kind || ''), text));
}
function addToolCall(ev) {
  const card = el('div', 'tool');
  const head = el('div', 'tool-head');
  head.appendChild(el('span', 'tool-dot'));
  head.appendChild(el('span', 'tool-name', ev.name));
  head.appendChild(el('span', 'tool-args', formatArgs(ev.input)));
  const status = el('span', 'tool-status running', 'running');
  head.appendChild(status);
  const body = el('pre', 'tool-body hidden');
  head.onclick = () => body.classList.toggle('hidden');
  card.appendChild(head);
  card.appendChild(body);
  add(card);
  toolCards[ev.id] = { body: body, status: status };
}
function setToolResult(ev) {
  const c = toolCards[ev.id];
  if (!c) return;
  c.status.textContent = ev.ok ? 'ok' : 'error';
  c.status.className = 'tool-status ' + (ev.ok ? 'ok' : 'err');
  c.body.textContent = ev.preview || '(no output)';
  if (!ev.ok) c.body.classList.remove('hidden');
  transcript.scrollTop = transcript.scrollHeight;
}
function addPermission(ev) {
  const card = el('div', 'perm');
  card.appendChild(el('div', 'perm-head', 'Permission needed'));
  card.appendChild(el('div', 'perm-label', ev.label));
  const actions = el('div', 'perm-actions');
  const choose = (decision) => {
    answerPermission(ev.id, decision);
    actions.remove();
    card.appendChild(el('div', 'perm-done', 'Decision: ' + decision));
  };
  const allow = el('button', 'p-allow', 'Allow once');
  allow.onclick = () => choose('once');
  const always = el('button', 'p-always', 'Always allow');
  always.onclick = () => choose('always');
  const deny = el('button', 'p-deny', 'Deny');
  deny.onclick = () => choose('deny');
  actions.appendChild(allow);
  actions.appendChild(always);
  actions.appendChild(deny);
  card.appendChild(actions);
  add(card);
}
function formatArgs(obj) {
  if (!obj || typeof obj !== 'object') return '';
  return Object.keys(obj).map(function (k) {
    let s = String(obj[k]).replace(/\s+/g, ' ');
    if (s.length > 54) s = s.slice(0, 51) + '…';
    return k + '=' + s;
  }).join('   ');
}

/* ---------- state ---------- */
function setBusy(b) {
  busy = b;
  sendBtn.disabled = b;
  input.disabled = b;
  cancelBtn.classList.toggle('hidden', !b);
  if (sessionId) meta.dataset.state = b ? 'working' : 'ready';
}

/* ---------- event handling ---------- */
function handleEvent(ev) {
  switch (ev.kind) {
    case 'connected': break;
    case 'assistant_text': addAssistant(ev.text); break;
    case 'tool_call': addToolCall(ev); break;
    case 'tool_result': setToolResult(ev); break;
    case 'permission_request': addPermission(ev); break;
    case 'permission_resolved': break;
    case 'notice': addNotice(ev.text, 'warn'); break;
    case 'error': addNotice('Error: ' + ev.text, 'err'); break;
    case 'turn_done': addNotice(ev.usage, 'usage'); break;
    case 'turn_end': setBusy(false); break;
    default: break;
  }
}

/* ---------- api ---------- */
async function answerPermission(id, decision) {
  try {
    await fetch('/api/sessions/' + sessionId + '/permission', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, decision: decision }),
    });
  } catch (e) { /* server will time the prompt out */ }
}
async function sendMessage() {
  const text = input.value.trim();
  if (!text || busy || !sessionId) return;
  addUser(text);
  input.value = '';
  autosize();
  setBusy(true);
  try {
    const r = await fetch('/api/sessions/' + sessionId + '/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text }),
    });
    if (!r.ok) {
      const d = await r.json().catch(function () { return {}; });
      addNotice('Could not send: ' + (d.detail || r.status), 'err');
      setBusy(false);
    }
  } catch (e) {
    addNotice('Could not reach the server.', 'err');
    setBusy(false);
  }
}
async function cancelRun() {
  if (!sessionId) return;
  try { await fetch('/api/sessions/' + sessionId + '/cancel', { method: 'POST' }); } catch (e) {}
  meta.textContent = 'cancelling…';
}

/* ---------- init ---------- */
function autosize() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 180) + 'px';
}
async function init() {
  let r;
  try {
    r = await fetch('/api/sessions', { method: 'POST' });
  } catch (e) {
    meta.textContent = 'server unreachable';
    return;
  }
  if (!r.ok) {
    const d = await r.json().catch(function () { return {}; });
    meta.textContent = 'not ready';
    addNotice('Cannot start a session: ' + (d.detail || r.status), 'err');
    return;
  }
  const data = await r.json();
  sessionId = data.session_id;
  meta.textContent = data.model + '  ·  ' + data.cwd;

  const es = new EventSource('/api/sessions/' + sessionId + '/stream');
  es.onmessage = function (e) {
    let ev;
    try { ev = JSON.parse(e.data); } catch (_) { return; }
    handleEvent(ev);
  };
  es.onerror = function () { meta.textContent = 'stream disconnected — reload to reconnect'; };

  setBusy(false);
  input.focus();
}

sendBtn.onclick = sendMessage;
cancelBtn.onclick = cancelRun;
input.addEventListener('input', autosize);
input.addEventListener('keydown', function (e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
document.querySelectorAll('.example').forEach(function (b) {
  b.onclick = function () { input.value = b.textContent; autosize(); input.focus(); };
});

init();
