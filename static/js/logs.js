const POLL_MS = 1500;

let dialog = null;
let listEl = null;
let emptyEl = null;
let statusEl = null;
let jobSelect = null;
let pollTimer = null;
let requestedJobId = null;
let requestGeneration = 0;
let lastRenderKey = "";
let previousFocus = null;

function formatTime(timestamp) {
  const date = new Date(Number(timestamp) * 1000);
  return Number.isNaN(date.getTime())
    ? "--:--:--"
    : date.toLocaleTimeString([], { hour12: false });
}

function renderEntries(entries) {
  const previousScrollTop = listEl.scrollTop;
  const wasNearBottom =
    listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight < 40;
  listEl.textContent = "";
  emptyEl.classList.toggle("hidden", entries.length > 0);
  for (const entry of entries) {
    const row = document.createElement("article");
    row.className = `log-entry log-${entry.level || "info"}`;

    const meta = document.createElement("div");
    meta.className = "log-entry-meta";
    const time = document.createElement("time");
    const parsedTime = new Date(Number(entry.timestamp) * 1000);
    if (!Number.isNaN(parsedTime.getTime())) time.dateTime = parsedTime.toISOString();
    time.textContent = formatTime(entry.timestamp);
    const level = document.createElement("span");
    level.className = "log-entry-level";
    level.textContent = String(entry.level || "info").toUpperCase();
    meta.append(time, level);

    if (entry.progress_percent != null) {
      const progress = document.createElement("span");
      progress.className = "log-entry-progress";
      progress.textContent = `${entry.progress_percent}%`;
      meta.append(progress);
    }

    const body = document.createElement("div");
    body.className = "log-entry-body";
    if (entry.stage) {
      const stage = document.createElement("span");
      stage.className = "log-entry-stage";
      stage.textContent = entry.stage;
      body.append(stage);
    }
    const message = document.createElement("p");
    message.textContent = entry.message || "";
    body.append(message);
    row.append(meta, body);
    listEl.append(row);
  }
  listEl.scrollTop = wasNearBottom ? listEl.scrollHeight : previousScrollTop;
}

function updateJobOptions(jobs) {
  const current = requestedJobId || jobSelect.value;
  const seen = new Set();
  const options = [{ value: "", label: "Current session / all jobs" }];
  for (const job of jobs || []) {
    if (!job.job_id || seen.has(job.job_id)) continue;
    seen.add(job.job_id);
    options.push({
      value: job.job_id,
      label: `${job.title || job.job_id} · ${job.status}`,
    });
  }
  if (current && !seen.has(current)) {
    options.push({ value: current, label: `${current} · selected job` });
  }
  jobSelect.textContent = "";
  for (const option of options) {
    const el = document.createElement("option");
    el.value = option.value;
    el.textContent = option.label;
    jobSelect.append(el);
  }
  jobSelect.value = current || "";
}

async function refreshLogs() {
  if (!dialog || dialog.classList.contains("hidden")) return;
  const generation = ++requestGeneration;
  const selected = requestedJobId || jobSelect.value;
  const query = new URLSearchParams({ limit: "300" });
  if (selected) query.set("job_id", selected);
  statusEl.textContent = "Refreshing...";
  try {
    const response = await fetch(`/api/logs?${query}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (generation !== requestGeneration) return;
    updateJobOptions(payload.jobs);
    requestedJobId = null;
    const entries = payload.entries || [];
    const renderKey = `${selected}:${entries.map((entry) => `${entry.id}:${entry.message}`).join("|")}`;
    if (renderKey !== lastRenderKey) {
      renderEntries(entries);
      lastRenderKey = renderKey;
    }
    statusEl.textContent = `${payload.entries?.length || 0} entries · auto refresh on`;
  } catch (error) {
    if (generation !== requestGeneration) return;
    statusEl.textContent = `Could not load logs: ${error?.message || String(error)}`;
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPolling() {
  stopPolling();
  refreshLogs();
  pollTimer = setInterval(refreshLogs, POLL_MS);
}

function closeLogViewer() {
  stopPolling();
  requestGeneration += 1;
  dialog?.classList.add("hidden");
  previousFocus?.focus?.();
  previousFocus = null;
}

export function openLogViewer(jobId = null) {
  if (!dialog) return;
  previousFocus = document.activeElement;
  requestedJobId = jobId;
  jobSelect.value = jobId || "";
  lastRenderKey = "";
  dialog.classList.remove("hidden");
  document.getElementById("logsClose")?.focus();
  startPolling();
}

export function initLogViewer() {
  dialog = document.getElementById("logsDialog");
  listEl = document.getElementById("logsList");
  emptyEl = document.getElementById("logsEmpty");
  statusEl = document.getElementById("logsStatus");
  jobSelect = document.getElementById("logsJobSelect");
  if (!dialog || !listEl || !emptyEl || !statusEl || !jobSelect) return;

  document.getElementById("logsBtn")?.addEventListener("click", () => openLogViewer());
  document.getElementById("logsClose")?.addEventListener("click", closeLogViewer);
  document.getElementById("logsRefresh")?.addEventListener("click", refreshLogs);
  jobSelect.addEventListener("change", () => {
    requestedJobId = jobSelect.value || null;
    refreshLogs();
  });
  dialog.addEventListener("mousedown", (event) => {
    if (event.target === dialog) closeLogViewer();
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.code === "Escape") closeLogViewer();
  });
}
