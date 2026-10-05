// UCO warehouse dashboard: draws the layout and live state streamed from /api/stream.
'use strict';
const canvas = document.getElementById('map');
const ctx = canvas.getContext('2d');
let layout = null;
let state = null;
const MARGIN = 20;

const STATUS_COLOR = {
  STORED: '#3b82f6', APPROVED: '#f59e0b', STORAGE_ASSIGNED: '#f59e0b', REGISTERED: '#94a3b8', WEIGHED: '#94a3b8',
  IN_TRANSIT: '#22d3ee', RESERVED: '#a855f7', DISPATCHED: '#22c55e', QUARANTINED: '#ef4444', REJECTED: '#ef4444',
};

function scale() {
  const sx = (canvas.width - 2 * MARGIN) / layout.size[0];
  const sy = (canvas.height - 2 * MARGIN) / layout.size[1];
  return Math.min(sx, sy);
}
const X = (x) => MARGIN + x * scale();
const Y = (y) => canvas.height - MARGIN - y * scale();
const rgb = (c, a = 1) => `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${a})`;

function drawLayout() {
  const s = scale();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#e2e8f0';
  ctx.fillRect(X(0), Y(layout.size[1]), layout.size[0] * s, layout.size[1] * s);
  for (const z of layout.zones) {
    const [x0, y0, x1, y1] = z.rect;
    ctx.fillStyle = rgb(z.color, z.kind === 'RESTRICTED' ? 0.25 : 0.12);
    ctx.fillRect(X(x0), Y(y1), (x1 - x0) * s, (y1 - y0) * s);
    ctx.strokeStyle = rgb(z.color, 0.9);
    ctx.lineWidth = 1.5;
    ctx.strokeRect(X(x0), Y(y1), (x1 - x0) * s, (y1 - y0) * s);
  }
  for (const o of layout.obstacles) {
    ctx.fillStyle = rgb(o.color, o.collision ? 0.9 : 0.3);
    if (o.shape === 'box') {
      ctx.fillRect(X(o.center[0] - o.size[0] / 2), Y(o.center[1] + o.size[1] / 2), o.size[0] * s, o.size[1] * s);
    } else {
      ctx.beginPath(); ctx.arc(X(o.center[0]), Y(o.center[1]), o.radius * s, 0, 2 * Math.PI); ctx.fill();
    }
  }
  ctx.strokeStyle = '#334155'; ctx.lineWidth = 4;
  ctx.strokeRect(X(0), Y(layout.size[1]), layout.size[0] * s, layout.size[1] * s);
  for (const d of layout.doors) {
    ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 6; ctx.beginPath();
    if (d.wall === 'west' || d.wall === 'east') {
      const x = d.wall === 'west' ? 0 : layout.size[0];
      ctx.moveTo(X(x), Y(d.span[0])); ctx.lineTo(X(x), Y(d.span[1]));
    } else {
      const y = d.wall === 'south' ? 0 : layout.size[1];
      ctx.moveTo(X(d.span[0]), Y(y)); ctx.lineTo(X(d.span[1]), Y(y));
    }
    ctx.stroke();
  }
}

function drawZoneLabels() {
  // drawn last, clipped to their own zone so neighbouring labels never overlap
  const s = scale();
  ctx.font = '11px system-ui';
  for (const z of layout.zones) {
    const [x0, y0, x1, y1] = z.rect;
    const text = z.name + (z.speed_limit ? ` ≤${z.speed_limit} m/s` : '') + (z.kind === 'RESTRICTED' ? ' · KEEP-OUT' : '');
    ctx.save();
    ctx.beginPath(); ctx.rect(X(x0), Y(y1), (x1 - x0) * s, (y1 - y0) * s); ctx.clip();
    const w = Math.min(ctx.measureText(text).width + 6, (x1 - x0) * s);
    ctx.fillStyle = 'rgba(255,255,255,0.75)';
    ctx.fillRect(X(x0) + 1, Y(y1) + 1, w, 15);
    ctx.fillStyle = '#1e293b';
    ctx.fillText(text, X(x0) + 4, Y(y1) + 12);
    ctx.restore();
  }
}

function drawState() {
  const s = scale();
  const slots = {};
  const containers = {};
  if (state.inventory) {
    for (const sl of state.inventory.slots) slots[sl.id] = sl;
    for (const c of state.inventory.containers) containers[c.id] = c;
  }
  for (const loc of layout.locations) {
    const sl = slots[loc.id];
    const w = 1.2 * s;
    ctx.lineWidth = 1;
    if (sl) {
      const c = sl.container ? containers[sl.container] : null;
      ctx.fillStyle = c ? (STATUS_COLOR[c.status] || '#64748b') : (sl.reserved ? '#fde68a' : '#ffffff');
      if (!sl.enabled) ctx.fillStyle = '#9ca3af';
      ctx.fillRect(X(loc.x) - w / 2, Y(loc.y) - w / 2, w, w);
      ctx.strokeStyle = '#64748b';
      ctx.strokeRect(X(loc.x) - w / 2, Y(loc.y) - w / 2, w, w);
      ctx.fillStyle = c ? '#fff' : '#475569';
      ctx.font = '9px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText(c ? c.id.replace('UCO-', '') : loc.id, X(loc.x), Y(loc.y) + 3);
      ctx.textAlign = 'left';
    } else {
      ctx.fillStyle = '#475569';
      ctx.font = '10px system-ui';
      ctx.fillText(loc.id, X(loc.x) - 14, Y(loc.y) + 4);
    }
  }
  // containers at stations (conveyor)
  if (state.inventory) {
    for (const c of state.inventory.containers) {
      const loc = layout.locations.find((l) => l.id === c.location && l.kind === 'STATION');
      if (loc) {
        ctx.fillStyle = STATUS_COLOR[c.status] || '#64748b';
        ctx.beginPath(); ctx.arc(X(loc.x), Y(loc.y), 0.45 * s, 0, 2 * Math.PI); ctx.fill();
      }
    }
  }
  if (state.trail && state.trail.length > 1) {
    ctx.strokeStyle = 'rgba(37,99,235,0.35)'; ctx.lineWidth = 2; ctx.beginPath();
    state.trail.forEach(([x, y], i) => (i ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))));
    ctx.stroke();
  }
  const a = state.agv;
  if (a && a.x !== null) {
    ctx.save();
    ctx.translate(X(a.x), Y(a.y));
    ctx.rotate(-a.theta);
    ctx.fillStyle = a.online ? (state.safety && !state.safety.motion_allowed ? '#dc2626' : '#15803d') : '#6b7280';
    ctx.fillRect(-0.5 * s, -0.35 * s, 1.0 * s, 0.7 * s);
    if (a.carrying) { ctx.fillStyle = '#22d3ee'; ctx.fillRect(-0.35 * s, -0.3 * s, 0.7 * s, 0.6 * s); }
    ctx.fillStyle = '#fde047';
    ctx.beginPath(); ctx.moveTo(0.55 * s, 0); ctx.lineTo(0.3 * s, 0.2 * s); ctx.lineTo(0.3 * s, -0.2 * s); ctx.fill();
    ctx.restore();
    ctx.fillStyle = '#0f172a'; ctx.font = 'bold 11px system-ui';
    ctx.fillText(`${a.id} ${a.state}`, X(a.x) + 0.7 * s, Y(a.y) - 0.6 * s);
  }
}

function td(v) { return `<td>${v === null || v === undefined ? '' : v}</td>`; }

function renderPanels() {
  const st = state.system_status || '?';
  const status = document.getElementById('status');
  status.textContent = `SYSTEM ${st}`; status.className = `badge ${st}`;
  const saf = state.safety || {};
  const sb = document.getElementById('safety');
  sb.textContent = `SAFETY ${saf.state || '?'}${saf.conditions && saf.conditions.length ? ': ' + saf.conditions.join(', ') : ''}`;
  sb.className = `badge ${saf.state || ''}`;
  document.getElementById('clock').textContent = new Date(state.wall_time * 1000).toLocaleTimeString();
  const a = state.agv;
  document.getElementById('agv').innerHTML = a ? `
    <div>State <b>${a.state}</b> ${a.online ? '' : '<span class="badge OFFLINE">OFFLINE</span>'}
      ${a.available ? '(available)' : '(busy)'}</div>
    <div>Task <b>${a.task || '-'}</b> &nbsp; goal ${a.goal || '-'} &nbsp; carrying ${a.carrying || '-'}</div>
    <div>Pose ${a.x === null ? '(not localised)' : `(${a.x.toFixed(2)}, ${a.y.toFixed(2)})`} &nbsp; speed ${a.speed} m/s &nbsp; limit ${saf.speed_limit ?? '-'} m/s</div>
    <div>Distance ${a.distance_m} m &nbsp; utilisation ${(a.utilization * 100).toFixed(0)} % &nbsp; zone ${saf.zone || '-'}</div>`
    : 'no AGV state';
  const pct = a && a.battery >= 0 ? a.battery : 0;
  const bar = document.getElementById('battery-bar');
  bar.style.width = `${pct}%`;
  bar.style.background = pct < 25 ? '#dc2626' : pct < 40 ? '#f59e0b' : '#22c55e';
  document.getElementById('battery-text').textContent =
    `Battery ${pct}% ${a && a.charging ? '⚡ charging' : ''} ${state.battery ? state.battery.voltage + ' V' : ''}`;
  const inv = state.inventory || {}; const t = state.tasks || {};
  document.getElementById('kpis').innerHTML = `
    <div>Storage occupancy<br><b>${inv.occupied ?? 0}/${inv.capacity ?? 0} (${((inv.occupancy || 0) * 100).toFixed(0)} %)</b></div>
    <div>Stored volume<br><b>${inv.volume_l ?? 0} L</b></div>
    <div>Received / dispatched<br><b>${inv.received ?? 0} / ${inv.dispatched ?? 0}</b></div>
    <div>Tasks done / failed<br><b>${t.completed ?? 0} / ${t.failed ?? 0}</b></div>
    <div>Avg task time<br><b>${t.avg_duration_s ?? '-'} s</b></div>
    <div>Throughput<br><b>${state.throughput_per_h ?? '-'} tasks/h</b></div>
    <div>Energy used<br><b>${state.energy_wh} Wh</b></div>
    <div>Active tasks<br><b>${(t.active || []).length}</b></div>`;
  const rows = [...(t.active || []), ...(t.recent || [])].slice(0, 18);
  document.getElementById('tasks').innerHTML = '<tr><th>Task</th><th>Type</th><th>Container</th><th>From → To</th>' +
    '<th>Status</th><th>Phase</th><th>AGV</th><th>Try</th><th>Time</th></tr>' + rows.map((r) =>
    `<tr>${td(r.id)}${td(r.type)}${td(r.container)}${td(r.source + ' → ' + r.destination)}${td(r.status)}` +
    `${td(r.phase)}${td(r.agv)}${td(r.attempts)}${td(r.duration !== null ? r.duration + ' s' : '')}</tr>`).join('');
  document.getElementById('inventory').innerHTML = '<tr><th>Container</th><th>Type</th><th>Volume</th><th>Status</th>' +
    '<th>Quality</th><th>Location</th><th>Destination</th></tr>' + (inv.containers || []).map((c) =>
    `<tr>${td(c.id)}${td(c.type)}${td(c.volume_l + ' L')}${td(c.status)}${td(c.quality)}${td(c.location)}` +
    `${td(c.destination)}</tr>`).join('');
  document.getElementById('alerts').innerHTML = (state.alerts || []).map((al) =>
    `<div class="alert sev-${al.severity}">[${al.severity}] ${al.t.toFixed(0)} s ${al.source}: ${al.message}</div>`).join('');
}

function render() {
  if (!layout || !state) return;
  drawLayout();
  drawState();
  drawZoneLabels();
  renderPanels();
}

async function post(path, body) {
  const out = document.getElementById('cmd-result');
  try {
    const r = await fetch(path, { method: 'POST', body: JSON.stringify(body) });
    const j = await r.json();
    out.textContent = `${j.ok ? '✔' : '✖'} ${j.message || ''}`;
  } catch (e) { out.textContent = `✖ ${e}`; }
}

function bind() {
  const v = (id) => document.getElementById(id).value;
  document.getElementById('btn-delivery').onclick = () => {
    const n = parseInt(v('del-count'), 10) || 1;
    const type = v('del-type');
    const vol = type === 'IBC_1000L' ? 850 : 180;
    post('/api/delivery', { supplier: 'Dashboard delivery', containers: Array.from({ length: n }, () => [type, vol]) });
  };
  document.getElementById('btn-processing').onclick = () => post('/api/processing', { count: parseInt(v('proc-count'), 10) });
  document.getElementById('btn-move').onclick = () => post('/api/move', { location: v('move-loc') });
  document.getElementById('btn-estop').onclick = () => post('/api/estop', { active: true });
  document.getElementById('btn-release').onclick = () => post('/api/estop', { active: false });
  const fault = (active) => post('/api/fault', { type: v('fault-type'), active, target: v('fault-target'),
    value: parseFloat(v('fault-value')) || 0, duration: active ? parseFloat(v('fault-dur')) || 0 : 0 });
  document.getElementById('btn-fault').onclick = () => fault(true);
  document.getElementById('btn-clear').onclick = () => fault(false);
}

async function start() {
  layout = await (await fetch('/api/layout')).json();
  bind();
  const es = new EventSource('/api/stream');
  es.onmessage = (ev) => { state = JSON.parse(ev.data); render(); };
  es.onerror = () => {
    const s = document.getElementById('status');
    s.textContent = 'DISCONNECTED'; s.className = 'badge ALARM';
  };
}
start();
