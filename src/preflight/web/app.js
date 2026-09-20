/* Report Preflight dashboard — no build step, no framework, no external data calls.
 * Everything it renders comes from this instance's own API. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { clients: [], slug: null, detail: null, run: null, view: "overview" };

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }
  if (!response.ok) {
    throw new Error((payload && payload.detail) || `${response.status} ${response.statusText}`);
  }
  return payload;
}

function showError(message) {
  const box = $("error");
  if (!message) {
    box.classList.add("hidden");
    return;
  }
  box.textContent = message;
  box.classList.remove("hidden");
}

function setLoading(on) {
  $("loading").classList.toggle("hidden", !on);
  $("run").disabled = on;
  $("run").textContent = on ? "Running…" : "Run preflight";
}

function statusClass(status) {
  return status === "fail" ? "is-fail" : status === "warn" ? "is-warn" : "is-pass";
}

function defaultPeriod() {
  const now = new Date();
  now.setDate(1);
  now.setMonth(now.getMonth() - 1);
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/* ---------- rendering ---------- */

function renderRun(run) {
  state.run = run;
  const hasRun = Boolean(run);
  $("empty").classList.toggle("hidden", hasRun);
  $("verdict").classList.toggle("hidden", !hasRun);
  $("notes-card").classList.toggle("hidden", !hasRun);
  $("checks-list").innerHTML = "";
  if (!hasRun) return;

  const pill = $("verdict-pill");
  pill.textContent = run.status.toUpperCase();
  pill.className = `pf-pill ${statusClass(run.status)}`;
  $("verdict-text").textContent = run.verdict;
  $("verdict-period").textContent = `${run.period} · ${run.checks.length} checks`;
  $("stat-total").textContent = run.counts.total;
  $("stat-failed").textContent = run.counts.blocking ?? run.counts.failed;
  $("stat-warned").textContent = run.counts.warned;
  $("stat-waived").textContent = run.counts.waived;

  for (const check of run.checks) {
    const card = document.createElement("details");
    card.className = "pf-check";
    const waived = check.waiver
      ? `<span class="pf-pill is-warn">accepted</span>`
      : "";
    card.innerHTML = `
      <summary>
        <span class="pf-chevron" aria-hidden="true">&#9656;</span>
        <span class="pf-pill ${statusClass(check.status)}">${check.status.toUpperCase()}</span>
        <span class="font-medium">${escapeHtml(check.check_id)}</span>
        <span class="text-sm text-slate-500 truncate">${escapeHtml(check.summary)}</span>
        ${waived}
      </summary>
      <div class="pf-body px-4 pb-4 space-y-3">
        ${check.note ? `<p class="text-sm text-slate-700">${escapeHtml(check.note)}</p>` : ""}
        <pre>${escapeHtml(JSON.stringify(check.detail, null, 2))}</pre>
      </div>`;
    $("checks-list").appendChild(card);
  }
  $("notes").textContent = run.notes_markdown || "";
}

function renderHistory(rows) {
  const body = $("history-body");
  body.innerHTML = "";
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="px-4 py-6 text-center text-slate-500">
      No runs recorded for this client yet.</td></tr>`;
    $("chart").innerHTML = "";
    return;
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="px-4 py-3">#${row.id}</td>
      <td class="px-4 py-3">${escapeHtml(row.period)}</td>
      <td class="px-4 py-3"><span class="pf-pill ${statusClass(row.status)}">
        ${row.status.toUpperCase()}</span></td>
      <td class="px-4 py-3">${row.total}</td>
      <td class="px-4 py-3">${row.failed}</td>
      <td class="px-4 py-3">${row.warned}</td>
      <td class="px-4 py-3 text-slate-500">${escapeHtml(row.created_at)}</td>`;
    tr.addEventListener("click", () => loadRun(row.id));
    tr.className = "cursor-pointer hover:bg-slate-50";
    body.appendChild(tr);
  }
  $("chart").innerHTML = barChart(rows.slice().reverse().slice(-12));
}

/* Hand-rolled SVG so the dashboard has no charting dependency.
 * Bar height encodes how many checks were not clean; colour encodes the verdict. */
function barChart(rows) {
  const width = Math.max(320, rows.length * 56);
  const height = 160;
  const floor = height - 28;
  const max = Math.max(1, ...rows.map((r) => r.total));
  const colours = { pass: "#10b981", warn: "#f59e0b", fail: "#ef4444" };
  const bars = rows
    .map((row, index) => {
      const unclean = row.failed + row.warned;
      const scaled = Math.max(4, Math.round((unclean / max) * (floor - 16)));
      const x = index * 56 + 12;
      const y = floor - scaled;
      return `<g>
        <rect x="${x}" y="${y}" width="32" height="${scaled}" rx="4"
              fill="${colours[row.status] || "#94a3b8"}"></rect>
        <text x="${x + 16}" y="${y - 6}" text-anchor="middle" font-size="11"
              fill="#475569">${unclean}</text>
        <text x="${x + 16}" y="${floor + 16}" text-anchor="middle" font-size="10"
              fill="#64748b">${escapeHtml(row.period.slice(2))}</text>
      </g>`;
    })
    .join("");
  return `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}"
      role="img" aria-label="Checks not clean, by period">
      <line x1="0" y1="${floor}" x2="${width}" y2="${floor}" stroke="#e2e8f0"></line>
      ${bars}
    </svg>`;
}

function renderChecks(detail) {
  const body = $("checks-body");
  body.innerHTML = "";
  const waivers = new Map((detail.waivers || []).map((w) => [w.check_id, w]));
  for (const check of detail.checks) {
    const waiver = waivers.get(check.id);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="px-4 py-3 font-medium">${escapeHtml(check.title)}</td>
      <td class="px-4 py-3"><code class="text-xs">${escapeHtml(check.type)}</code></td>
      <td class="px-4 py-3 text-slate-600">${escapeHtml(check.description)}</td>
      <td class="px-4 py-3"></td>`;
    const cell = tr.lastElementChild;
    if (waiver) {
      cell.innerHTML = `<span class="pf-pill is-warn">accepted</span>
        <div class="mt-1 text-xs text-slate-500">${escapeHtml(waiver.reason)}</div>`;
    } else {
      const button = document.createElement("button");
      button.className = "pf-btn-ghost";
      button.textContent = "Accept…";
      button.addEventListener("click", () => waive(check.id));
      cell.appendChild(button);
    }
    body.appendChild(tr);
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]
  );
}

/* ---------- actions ---------- */

async function waive(checkId) {
  const reason = window.prompt(
    "Client-safe reason for accepting this check (it is quoted in the data notes):"
  );
  if (!reason) return;
  const by = window.prompt("Who is accepting it?");
  if (!by) return;
  try {
    await api(`/api/clients/${state.slug}/waivers`, {
      method: "POST",
      body: JSON.stringify({ check_id: checkId, reason, waived_by: by }),
    });
    await selectClient(state.slug);
    showError("");
  } catch (error) {
    showError(error.message);
  }
}

async function runPreflight() {
  const period = $("period").value.trim();
  if (!/^\d{4}-\d{2}$/.test(period)) {
    showError("Period must look like 2026-08.");
    return;
  }
  showError("");
  setLoading(true);
  try {
    const run = await api("/api/runs", {
      method: "POST",
      body: JSON.stringify({ client: state.slug, period }),
    });
    renderRun(run);
    await loadHistory();
    switchView("overview");
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false);
  }
}

async function loadRun(runId) {
  setLoading(true);
  try {
    const run = await api(`/api/runs/${runId}`);
    renderRun({
      status: run.status,
      verdict: run.verdict,
      period: run.period,
      counts: {
        total: run.total,
        failed: run.failed,
        warned: run.warned,
        waived: run.waived,
      },
      checks: run.checks,
      notes_markdown: run.notes_markdown,
    });
    switchView("overview");
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false);
  }
}

async function loadHistory() {
  const rows = await api(`/api/clients/${state.slug}/runs?limit=20`);
  renderHistory(rows);
}

async function selectClient(slug) {
  state.slug = slug;
  showError("");
  setLoading(true);
  try {
    const detail = await api(`/api/clients/${slug}`);
    state.detail = detail;
    $("client-title").textContent = detail.name;
    $("client-meta").textContent = `${detail.checks.length} checks · ${detail.sources.length} sources`;
    renderChecks(detail);
    renderRun(null);
    await loadHistory();
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false);
  }
}

function switchView(view) {
  state.view = view;
  for (const name of ["overview", "history", "checks"]) {
    $(`view-${name}`).classList.toggle("hidden", name !== view);
  }
  document.querySelectorAll(".pf-nav").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === view);
  });
}

async function boot() {
  $("period").value = defaultPeriod();
  $("run").addEventListener("click", runPreflight);
  $("client").addEventListener("change", (event) => selectClient(event.target.value));
  $("copy-notes").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("notes").textContent);
      $("copy-notes").textContent = "Copied";
      setTimeout(() => ($("copy-notes").textContent = "Copy"), 1200);
    } catch {
      showError("Clipboard not available in this browser context.");
    }
  });
  $("nav-toggle").addEventListener("click", () => {
    const body = $("nav-body");
    const open = body.classList.toggle("hidden");
    $("nav-toggle").setAttribute("aria-expanded", String(!open));
  });
  document.querySelectorAll(".pf-nav").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });

  try {
    const health = await api("/api/health");
    $("version").textContent = `v${health.version}`;
    const clients = await api("/api/clients");
    state.clients = clients;
    const select = $("client");
    select.innerHTML = clients
      .map((client) => `<option value="${escapeHtml(client.slug)}">${escapeHtml(client.name)}</option>`)
      .join("");
    if (!clients.length) {
      $("client-title").textContent = "No clients configured";
      showError("No client configuration found. Add clients/<slug>/preflight.yml and reload.");
      return;
    }
    await selectClient(clients[0].slug);
  } catch (error) {
    $("client-title").textContent = "Unavailable";
    showError(error.message);
  }
}

document.addEventListener("DOMContentLoaded", boot);
