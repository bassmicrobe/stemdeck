import {
  form, urlInput, submitBtn, errorEl, jobBox, jobTitleEl, jobStageEl,
  jobDetailEl, jobEtaEl, jobPercentEl, jobCancelBtn, progressEl, titleEl, bpmChip, keyChip,
  eventSource, setEventSource, setCurrentJobId, currentJobId,
  demucsDevicePreset, effectiveSelectedStems, qualityPreset, selectedStems, stemDenoisePreset,
} from "./state.js";
import { destroyPlayer, wireUpAudio, setWaveformLoading, updateFooterTrack } from "./player.js";
import { stagePhrases } from "./phrases.js";
import {
  addTrackToLibrary,
  applyStemPresenceCards,
  getCurrentTrack,
  isTrackPlayable,
  setCurrentTrack,
  updateTrackStatus,
} from "./catalog.js";
import { initSections } from "./sections.js";
import { supportedStemNamesForQuality } from "./constants.js";
import { fmtBeatGrid } from "./utils.js";
import { openLogViewer } from "./logs.js";

// Playful stage label rotation (Claude-Code-style flair). The backend
// emits truthful stage strings; we surface them in the small #job-detail
// line so progress is debuggable, while #job-stage rotates whimsy.
const ROTATION_MS = 2500;
let phraseTimerId = null;
let lastStatus = null;
let jobPollTimerId = null;
let activeJobSyncTimerId = null;
let jobTimerId = null;
let jobTimerState = null;
let monitoredJobId = null;
let monitoredJobOwnsStudio = false;
const renderedJobs = new Set();
const jobSources = new Map();
const activeJobIds = new Set();
let activeJobSyncFailures = 0;

const TERMINAL_STATUSES = new Set(["done", "error", "cancelled"]);

function normalizeStemsForQuality(stems, preset) {
  const allowed = supportedStemNamesForQuality(preset);
  const selected = (stems?.length ? stems : [...selectedStems]).filter((name) =>
    allowed.includes(name)
  );
  return selected.length > 0 ? selected : [...allowed];
}

function setSubmitProcessing(processing) {
  submitBtn.disabled = processing;
  submitBtn.classList.toggle("loading", processing);
  document.querySelector(".strip-sq-process")?.classList.toggle("loading", processing);
  const label = submitBtn.querySelector("span");
  if (label) label.textContent = processing ? "Processing" : "Process";
}

function setJobConnectionStatus(message = "", tone = "info") {
  let el = jobBox.querySelector(".job-connection");
  if (!message) {
    el?.remove();
    return;
  }
  if (!el) {
    el = document.createElement("div");
    el.className = "job-connection";
    jobBox.insertBefore(el, jobBox.querySelector(".job-progress-row") || jobCancelBtn);
  }
  el.className = `job-connection ${tone}`;
  el.textContent = message;
}

function shouldForegroundNewJob() {
  if (!jobBox.classList.contains("hidden")) return false;
  return !isTrackPlayable(getCurrentTrack());
}

function showJobMonitor(jobId, status = "queued", { ownStudio = false } = {}) {
  monitoredJobId = jobId;
  monitoredJobOwnsStudio = ownStudio;
  if (ownStudio && !isTrackPlayable(getCurrentTrack())) setCurrentTrack(jobId);
  jobBox.classList.remove("hidden");
  jobCancelBtn.classList.remove("hidden");
  startPhraseRotation(status);
  lastStatus = status;
  connectEvents(jobId);
}

export function showJobProgress(jobId, { ownStudio = false } = {}) {
  if (!jobId) return;
  showJobMonitor(jobId, "queued", { ownStudio });
  probeJob(jobId).catch((err) => {
    console.warn("[job] could not open job monitor:", err);
  });
}

function formatClock(seconds) {
  const n = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  const s = n % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function epochMs(seconds) {
  const n = Number(seconds);
  return Number.isFinite(n) && n > 0 ? n * 1000 : null;
}

function timerFromState(state, pct = null) {
  const localNow = Date.now();
  const serverNow = epochMs(state.server_time) ?? localNow;
  return {
    jobId: state.job_id,
    status: state.status,
    progressPercent: pct ?? Math.round(state.progress_percent ?? ((state.progress || 0) * 100)),
    timerStartedAtMs: epochMs(state.timer_started_at ?? state.created_at),
    processingStartedAtMs: epochMs(state.processing_started_at),
    completedAtMs: epochMs(state.completed_at),
    serverOffsetMs: serverNow - localNow,
    etaSeconds: state.eta_seconds,
    etaSampledAtMs: serverNow,
    queuePosition: state.queue_position,
    queueSize: state.queue_size,
  };
}

function serverNowMs(timer) {
  return Date.now() + (timer?.serverOffsetMs ?? 0);
}

function elapsedFrom(timer, startedAtMs) {
  if (!startedAtMs) return null;
  const endedAtMs = timer.completedAtMs ?? serverNowMs(timer);
  return Math.max(0, (endedAtMs - startedAtMs) / 1000);
}

function etaFrom(timer) {
  if (timer.etaSeconds == null) return null;
  const elapsedSinceSample = Math.max(0, (serverNowMs(timer) - timer.etaSampledAtMs) / 1000);
  return Math.max(0, Number(timer.etaSeconds) - elapsedSinceSample);
}

function timerLabel(timer) {
  if (!timer || TERMINAL_STATUSES.has(timer.status)) return "";
  if (timer.status === "queued") {
    const waiting = elapsedFrom(timer, timer.timerStartedAtMs);
    const waitingLabel = waiting != null ? `Waiting ${formatClock(waiting)}` : "Waiting";
    if (timer.queuePosition != null) {
      const queueLabel = `Queue #${timer.queuePosition}${timer.queueSize ? ` of ${timer.queueSize}` : ""}`;
      return [queueLabel, waitingLabel].filter(Boolean).join(" · ");
    }
    return waitingLabel;
  }

  const startedAtMs = timer.processingStartedAtMs ?? timer.timerStartedAtMs;
  const elapsed = elapsedFrom(timer, startedAtMs);
  const elapsedLabel = elapsed != null ? `Elapsed ${formatClock(elapsed)}` : "";
  const eta = etaFrom(timer);
  if (eta != null) {
    return [elapsedLabel, `ETA ${formatClock(eta)}`].filter(Boolean).join(" · ");
  }
  if (timer.status === "separating" && timer.progressPercent > 0 && timer.progressPercent < 100) {
    return [elapsedLabel, "ETA estimating..."].filter(Boolean).join(" · ");
  }
  return elapsedLabel;
}

function etaLabel(state, pct) {
  return timerLabel(timerFromState(state, pct));
}

function renderJobTimer() {
  if (!jobTimerState) return;
  jobEtaEl.textContent = timerLabel(jobTimerState);
}

function syncJobTimer(state, pct) {
  if (!state?.job_id || TERMINAL_STATUSES.has(state.status)) {
    stopJobTimer();
    return;
  }
  jobTimerState = timerFromState(state, pct);
  renderJobTimer();
  if (!jobTimerId) jobTimerId = setInterval(renderJobTimer, 1000);
}

function stopJobTimer() {
  if (jobTimerId) {
    clearInterval(jobTimerId);
    jobTimerId = null;
  }
  jobTimerState = null;
}

function pickPhrase(status) {
  const pool = stagePhrases[status] || stagePhrases.default;
  return pool[Math.floor(Math.random() * pool.length)];
}

function setOverlayPhrase(text) {
  const el = document.getElementById("waveLoadingPhrase");
  if (el) el.textContent = text;
}

function startPhraseRotation(status) {
  stopPhraseRotation();
  const phrase = pickPhrase(status);
  jobStageEl.textContent = phrase;
  setOverlayPhrase(phrase);
  phraseTimerId = setInterval(() => {
    const p = pickPhrase(status);
    jobStageEl.textContent = p;
    setOverlayPhrase(p);
  }, ROTATION_MS);
}

function stopPhraseRotation() {
  if (phraseTimerId) {
    clearInterval(phraseTimerId);
    phraseTimerId = null;
  }
  jobStageEl.textContent = "";
}

function stopJobPolling() {
  if (jobPollTimerId) {
    clearInterval(jobPollTimerId);
    jobPollTimerId = null;
  }
}

export function showError(message) {
  errorEl.textContent = "";
  const msg = document.createElement("div");
  msg.className = "error-msg";
  msg.textContent = message;
  const retry = document.createElement("button");
  retry.className = "retry-btn";
  retry.type = "button";
  retry.textContent = "Try again";
  retry.addEventListener("click", () => {
    errorEl.classList.add("hidden");
    urlInput.focus();
    urlInput.select();
  });
  errorEl.append(msg, retry);
  errorEl.classList.remove("hidden");
}

export function reset() {
  if (eventSource) {
    eventSource.close();
    setEventSource(null);
  }
  stopJobPolling();
  stopJobTimer();
  stopPhraseRotation();
  lastStatus = null;
  monitoredJobId = null;
  monitoredJobOwnsStudio = false;
  destroyPlayer();
  errorEl.classList.add("hidden");
  errorEl.textContent = "";
  jobBox.classList.add("hidden");
  jobCancelBtn.classList.add("hidden");
  jobTitleEl.textContent = "";
  jobStageEl.textContent = "";
  jobDetailEl.textContent = "";
  jobEtaEl.textContent = "";
  jobPercentEl.textContent = "0%";
  progressEl.value = 0;
  setJobConnectionStatus("");
  setSubmitProcessing(false);
  setCurrentJobId(null);
}

function channelForStatus(state) {
  if (state.status === "done") return "Extracted";
  if (state.status === "queued") return "Queued";
  return "Processing";
}

function applyState(state, { focus = true, ownStudio = monitoredJobOwnsStudio } = {}) {
  const currentTrack = getCurrentTrack();
  const ownsStudio = focus && ownStudio && (
    currentJobId === state.job_id
    || !isTrackPlayable(currentTrack)
    || currentTrack?.id === state.job_id
  );

  if (state.job_id) {
    addTrackToLibrary({
      id: state.job_id,
      title: state.title || state.source_url || urlInput.value || "Processing track",
      channel: channelForStatus(state),
      thumb: state.thumbnail,
      stems: state.selected_stems || state.stems?.map((stem) => stem.name) || [...selectedStems],
      selectedStems: state.selected_stems || [...selectedStems],
      audioStems: state.stems || [],
      status: state.status,
      progressPercent: state.progress_percent ?? null,
      queuePosition: state.queue_position ?? null,
      queueSize: state.queue_size ?? 0,
      stage: state.stage || "",
      duration: state.duration,
      bpm: state.bpm,
      key: state.key,
      scale: state.scale,
      keyConfidence: state.key_confidence,
      lufs: state.lufs,
      peakDb: state.peak_db,
      dynamicRange: state.dynamic_range,
      tempoStability: state.tempo_stability,
      beatTimes: state.beat_times ?? null,
      chordProgression: state.chord_progression ?? null,
      chordMidiUrl: state.chord_midi_url ?? null,
      midiAnalysisUrl: state.midi_analysis_url ?? null,
      stemPresence: state.stem_presence,
      bassRepairApplied: state.bass_repair_applied ?? false,
      phaseRepairApplied: state.phase_repair_applied ?? false,
      phaseRepairResidualRatio: state.phase_repair_residual_ratio ?? null,
      stemDenoisePreset: state.stem_denoise_preset || "off",
      demucsDevice: state.demucs_device || "auto",
      demucsDeviceResolved: state.demucs_device_resolved || "",
      stemDenoiseApplied: state.stem_denoise_applied ?? false,
      stemGateApplied: state.stem_gate_applied ?? false,
      stemGateThresholdDb: state.stem_gate_threshold_db ?? null,
      profileKey: state.profile_key,
      profileLabel: state.profile_label,
      processingSeconds: state.processing_elapsed_seconds ?? state.total_elapsed_seconds ?? null,
      processingStartedAt: state.processing_started_at ?? null,
      timerStartedAt: state.timer_started_at ?? state.created_at ?? null,
      completedAt: state.completed_at ?? null,
      sourceUrl: jobSources.get(state.job_id) || state.source_url || urlInput.value,
      createdAt: state.created_at,
    });
    if (focus && !isTrackPlayable(getCurrentTrack())) setCurrentTrack(state.job_id);
  }
  const terminal = TERMINAL_STATUSES.has(state.status);
  if (terminal) activeJobIds.delete(state.job_id);

  if (!focus) {
    if (state.status === "done") updateTrackStatus(state.job_id, "done");
    else if (state.status === "error") updateTrackStatus(state.job_id, "error");
    else if (state.status === "cancelled") updateTrackStatus(state.job_id, "cancelled");
    return;
  }
  if (state.title) {
    jobTitleEl.textContent = state.title;
    if (ownsStudio) titleEl.textContent = state.title;
  }
  if (ownsStudio && state.bpm) bpmChip.textContent = `${state.bpm} BPM`;
  if (ownsStudio && state.key) keyChip.textContent = state.key;
    if (ownsStudio && (state.title || state.bpm || state.key || state.thumbnail)) {
      updateFooterTrack({
        title: state.title,
        thumbnail: state.thumbnail,
        key: state.key,
        bpm: state.bpm,
        profileLabel: state.profile_label,
        stemCount: state.stems ? state.stems.filter((s) => s.name !== "original").length : null,
      });
  }
  const summaryKey = document.getElementById("summary-key");
  const summaryBpm = document.getElementById("summary-bpm");
  const summaryScale = document.getElementById("summary-scale");
  const summaryScaleName = document.getElementById("summary-scale-name");
  const summaryConfidence = document.getElementById("summary-confidence");
  const summaryConfidenceLabel = document.getElementById("summary-confidence-label");
  const summaryLufs = document.getElementById("summary-lufs");
  const summaryPeak = document.getElementById("summary-peak");
  const summaryDuration = document.getElementById("summary-duration");
  const trackProcessed = document.getElementById("track-processed");
  const trackBeats = document.getElementById("track-beats");
  if (ownsStudio && summaryKey && state.key) summaryKey.textContent = state.key;
  if (ownsStudio && summaryBpm && state.bpm) summaryBpm.textContent = String(state.bpm);
  if (ownsStudio && summaryScale && state.scale) summaryScale.textContent = state.scale;
  if (ownsStudio && summaryScaleName && state.scale) summaryScaleName.textContent = state.scale;
  if (ownsStudio && summaryLufs && state.lufs != null) summaryLufs.textContent = state.lufs.toFixed(1);
  if (ownsStudio && summaryPeak && state.peak_db != null) summaryPeak.textContent = `Peak ${state.peak_db.toFixed(1)} dB`;
  if (ownsStudio && summaryDuration && state.duration) {
    const m = Math.floor(state.duration / 60);
    const s = Math.floor(state.duration % 60).toString().padStart(2, "0");
    summaryDuration.textContent = `${m.toString().padStart(2, "0")}:${s}`;
  }
  if (ownsStudio && trackProcessed) {
    const elapsed = state.processing_elapsed_seconds ?? state.total_elapsed_seconds;
    trackProcessed.textContent = elapsed != null ? formatClock(elapsed) : "—";
  }
  if (ownsStudio && trackBeats) {
    trackBeats.textContent = fmtBeatGrid(state.beat_times, state.duration);
  }
  if (ownsStudio && summaryConfidence && state.key_confidence != null) {
    const confidence = Math.max(0, Math.min(100, Number(state.key_confidence)));
    const confSpan = document.createElement("span");
    confSpan.textContent = `${confidence}%`;
    summaryConfidence.textContent = "";
    summaryConfidence.appendChild(confSpan);
    summaryConfidence.style.setProperty("--confidence-pct", confidence);
    summaryConfidence.classList.remove("hidden");
    summaryConfidenceLabel?.classList.remove("hidden");
  }
  const summaryDr = document.getElementById("summary-dr");
  const summaryDrLabel = document.getElementById("summary-dr-label");
  const summaryStability = document.getElementById("summary-stability");
  const summaryStabilityLabel = document.getElementById("summary-stability-label");
  if (ownsStudio && summaryDr && state.dynamic_range != null) summaryDr.textContent = String(state.dynamic_range);
  if (ownsStudio && summaryDrLabel && state.dynamic_range != null) {
    const dr = state.dynamic_range;
    summaryDrLabel.textContent = dr < 7 ? "Compressed" : dr < 10 ? "Moderate" : dr < 14 ? "High" : "Wide";
  }
  if (ownsStudio && summaryStability && state.tempo_stability != null) {
    summaryStability.textContent = `${state.tempo_stability}%`;
    summaryStability.className = "meta-card-value" + (state.tempo_stability >= 80 ? " stability-high" : "");
  }
  if (ownsStudio && summaryStabilityLabel && state.tempo_stability != null) {
    const s = state.tempo_stability;
    summaryStabilityLabel.textContent = s >= 90 ? "Very Stable" : s >= 70 ? "Stable" : s >= 50 ? "Moderate" : "Variable";
  }
  if (ownsStudio && state.stem_presence != null) {
    applyStemPresenceCards(state.stem_presence);
  }
  // Stage label is owned by the phrase-rotation timer below; we don't
  // overwrite it from each SSE tick. The truthful backend stage goes
  // to the small detail line instead.
  jobDetailEl.textContent = state.stage || "";
  const pct = Math.max(0, Math.min(100, Math.round(state.progress_percent ?? ((state.progress || 0) * 100))));
  progressEl.value = pct;
  jobPercentEl.textContent = `${pct}%`;
  jobEtaEl.textContent = etaLabel(state, pct);
  syncJobTimer(state, pct);

  // Cancel button is visible exactly while the job is in a non-terminal state.
  jobCancelBtn.classList.toggle("hidden", terminal);

  if (state.status !== lastStatus) {
    if (terminal) stopPhraseRotation();
    else startPhraseRotation(state.status);
    lastStatus = state.status;
  }

  if (state.status === "error") {
    stopJobPolling();
    stopJobTimer();
    updateTrackStatus(state.job_id, "error");
    if (ownsStudio) setWaveformLoading(false);
    showError(state.error || "Unknown error");
    setSubmitProcessing(false);
  } else if (state.status === "cancelled") {
    stopJobPolling();
    stopJobTimer();
    updateTrackStatus(state.job_id, "cancelled");
    if (ownsStudio) setWaveformLoading(false);
    if (monitoredJobId === state.job_id) {
      monitoredJobId = null;
      monitoredJobOwnsStudio = false;
      jobBox.classList.add("hidden");
    }
    setSubmitProcessing(false);
  } else if (state.status === "done") {
    stopJobPolling();
    stopJobTimer();
    updateTrackStatus(state.job_id, "done");
    if (monitoredJobId === state.job_id) {
      monitoredJobId = null;
      monitoredJobOwnsStudio = false;
      jobBox.classList.add("hidden");
    }
    if (ownsStudio && !renderedJobs.has(state.job_id)) {
      renderedJobs.add(state.job_id);
      wireUpAudio(
        state.job_id,
        state.stems || [],
        state.duration || 0,
        state.thumbnail,
        state.mix_url ?? null,
        state.title || "",
        null,
        state.profile_label || "",
        state.profile_key || "",
        state.beat_times || [],
        state.chord_midi_url || null,
        state.midi_analysis_url || null,
      );
      initSections(state.job_id, state.sections, state.duration || 0);
    }
    setSubmitProcessing(false);
  }
}

async function probeJob(jobId, options = {}) {
  const r = await fetch(`/api/jobs/${jobId}`);
  if (!r.ok) {
    if (r.status === 404) throw new Error("Job no longer exists on the server");
    throw new Error(`Job probe failed: ${r.status}`);
  }
  const s = await r.json();
  applyState(s, options);
  return s;
}

function startJobPolling(jobId) {
  stopJobPolling();
  const tick = async () => {
    try {
      const s = await probeJob(jobId);
      if (TERMINAL_STATUSES.has(s.status)) stopJobPolling();
    } catch (err) {
      console.warn("[job] REST fallback failed:", err);
    }
  };
  tick();
  jobPollTimerId = setInterval(tick, 1000);
}

async function syncActiveJobs() {
  try {
    const res = await fetch("/api/jobs/active", { cache: "no-store" });
    if (!res.ok) return;
    activeJobSyncFailures = 0;
    setJobConnectionStatus("");
    const states = await res.json();
    const nextIds = new Set(states.map((state) => state.job_id));
    for (const state of states) {
      activeJobIds.add(state.job_id);
      applyState(state, { focus: false });
    }
    for (const id of [...activeJobIds]) {
      if (nextIds.has(id)) continue;
      activeJobIds.delete(id);
      try {
        await probeJob(id, { focus: false });
      } catch {
        /* job may have been swept or deleted */
      }
    }
  } catch (err) {
    activeJobSyncFailures += 1;
    if (activeJobSyncFailures >= 2) {
      setJobConnectionStatus("Reconnecting to background queue...", "warn");
    }
    console.warn("[job] active queue sync failed:", err);
  }
}

function startActiveJobSync() {
  if (activeJobSyncTimerId) return;
  syncActiveJobs();
  activeJobSyncTimerId = setInterval(syncActiveJobs, 2500);
}

// Connect (or reconnect) to the SSE stream for a job. On unexpected
// disconnect we probe /api/jobs/{id} to decide: if the job is already
// terminal, accept its final state; otherwise reconnect with backoff.
// Falls back to REST polling only after SSE exhausts its retry budget.
function connectEvents(jobId) {
  let attempt = 0;
  let stopped = false;

  const open = () => {
    if (monitoredJobId !== jobId) return;
    if (eventSource) {
      eventSource.close();
      setEventSource(null);
    }
    const es = new EventSource(`/api/jobs/${jobId}/events`);
    setEventSource(es);

    es.onopen = () => {
      attempt = 0;
      setJobConnectionStatus("");
    };

    es.onmessage = (ev) => {
      attempt = 0; // any successful frame resets backoff
      setJobConnectionStatus("");
      let s;
      try { s = JSON.parse(ev.data); } catch { return; }
      // Defer by one tick so synchronous user event handlers (clicks,
      // input events) always complete before SSE state is applied.
      setTimeout(() => {
        if (monitoredJobId !== jobId) return;
        applyState(s, { ownStudio: monitoredJobOwnsStudio });
        if (TERMINAL_STATUSES.has(s.status)) {
          stopped = true;
          es.close();
          setEventSource(null);
        }
      }, 0);
    };

    es.onerror = async () => {
      if (stopped) return;
      if (monitoredJobId !== jobId) {
        stopped = true;
        es.close();
        return;
      }
      es.close();
      setEventSource(null);

      // Probe REST once before declaring failure -- handles dev-server
      // reloads and brief network blips where the job is actually fine.
      try {
        const s = await probeJob(jobId, { ownStudio: monitoredJobOwnsStudio });
        if (TERMINAL_STATUSES.has(s.status)) {
          stopped = true;
          return;
        }
      } catch (err) {
        if (err.message === "Job no longer exists on the server") {
          stopped = true;
          showError(err.message);
          setSubmitProcessing(false);
          return;
        }
        // Network down -- fall through to backoff.
      }

      attempt += 1;
      if (attempt > 6) {
        // SSE gave up — activate REST polling as the fallback.
        setJobConnectionStatus("Live updates interrupted. Polling job status...", "warn");
        startJobPolling(jobId);
        return;
      }
      // 0.5s, 1s, 2s, 4s, 8s, 16s
      const delay = 500 * Math.pow(2, attempt - 1);
      setJobConnectionStatus(`Connection interrupted. Retrying in ${Math.round(delay / 1000)}s...`, "warn");
      setTimeout(() => { if (!stopped) open(); }, delay);
    };
  };

  open();
}

async function cancelCurrentJob() {
  const id = monitoredJobId;
  if (!id) return;
  jobCancelBtn.disabled = true;
  jobCancelBtn.textContent = "Cancelling…";
  try {
    await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
    // The next SSE frame (or the REST probe in connectEvents) will
    // surface the cancelled state and hide the button via applyState.
  } catch {
    /* SSE will reflect the result regardless */
  } finally {
    jobCancelBtn.disabled = false;
    jobCancelBtn.textContent = "Cancel";
  }
}

function sanitizeFilename(name) {
  // Strip extension, collapse whitespace, cap at 120 chars — mirrors the
  // backend _sanitize_title() so title and sourceUrl match on both sides.
  return name
    .replace(/\.[^.]+$/, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
}

// Programmatic URL import. Used by the library "Sync again" auto-restore to
// re-download + re-separate a track whose backend audio was swept. If a
// playable track is already selected, the restore runs in the background.
export async function importFromUrl(url, { title, stems, quality, denoise, device } = {}) {
  if (!url || url.startsWith("local:")) return null; // local files can't auto-restore
  const foreground = shouldForegroundNewJob();
  if (foreground) {
    reset();
    setWaveformLoading(true, "");
  } else {
    errorEl.classList.add("hidden");
  }
  setSubmitProcessing(true);
  const preset = quality || qualityPreset;
  const denoisePreset = denoise || stemDenoisePreset;
  const devicePreset = device || demucsDevicePreset;
  const stemSel = normalizeStemsForQuality(stems, preset);

  let jobId;
  try {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        stems: stemSel,
        quality_preset: preset,
        stem_denoise: denoisePreset,
        demucs_device: devicePreset,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.statusText);
    jobId = data.job_id;
  } catch (err) {
    if (foreground) setWaveformLoading(false);
    showError(`Failed to restore track: ${err.message}`);
    setSubmitProcessing(false);
    return null;
  }

  jobSources.set(jobId, url);
  activeJobIds.add(jobId);
  // Merges into the existing library entry by sourceUrl (replaceTrackId),
  // preserving its folder placement; status updates as SSE frames arrive.
  addTrackToLibrary({
    id: jobId,
    title: title || url || "Processing track",
    channel: "Queued",
    thumb: "",
    stems: stemSel,
    selectedStems: stemSel,
    qualityPreset: preset,
    stemDenoisePreset: denoisePreset,
    demucsDevice: devicePreset,
    demucsDeviceResolved: "",
    audioStems: [],
    status: "queued",
    progressPercent: 0,
    queuePosition: null,
    queueSize: 0,
    bpm: null,
    key: null,
    scale: null,
    keyConfidence: null,
    lufs: null,
    peakDb: null,
    bassRepairApplied: false,
    phaseRepairApplied: false,
    phaseRepairResidualRatio: null,
    stemDenoiseApplied: false,
    stemGateApplied: false,
    stemGateThresholdDb: null,
    processingStartedAt: null,
    timerStartedAt: null,
    processingSeconds: null,
    completedAt: null,
    sourceUrl: url,
  });
  showJobMonitor(jobId, "queued", { ownStudio: foreground });
  syncActiveJobs();
  setSubmitProcessing(false);
  return jobId;
}

export function wireJobForm() {
  jobCancelBtn.addEventListener("click", cancelCurrentJob);
  document.getElementById("job-logs")?.addEventListener("click", () => {
    openLogViewer(monitoredJobId);
  });
  startActiveJobSync();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const foreground = shouldForegroundNewJob();
    if (foreground) reset();
    else errorEl.classList.add("hidden");
    setSubmitProcessing(true);

    const fileInput = document.getElementById("fileInput");
    // Prefer _file cache: browsers (WKWebView, Chromium) silently clear
    // fileInput.files after a fetch() submission, breaking re-submits.
    const file = fileInput?._file ?? fileInput?.files?.[0] ?? null;
    const sanitized = file ? sanitizeFilename(file.name) : null;
    const sourceUrl = file ? `local:${sanitized}` : urlInput.value;
    const displayTitle = sanitized ?? (urlInput.value || "Processing track");
    const preset = qualityPreset;
    const denoisePreset = stemDenoisePreset;
    const devicePreset = demucsDevicePreset;
    const stemSel = effectiveSelectedStems(preset);

    const postUrlText = document.getElementById("post-url-text");
    if (postUrlText) postUrlText.textContent = displayTitle;

    // If no playable track is loaded yet, keep the empty studio in a loading
    // state. The job monitor itself is non-modal, so queueing never blocks
    // library browsing, playback, export, or adding another track.
    if (foreground) setWaveformLoading(true, file ? "Uploading…" : "");
    if (file && foreground) {
      lastStatus = "queued";
    }

    let fetchInit;
    if (file) {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("stems", JSON.stringify(stemSel));
      fd.append("quality_preset", preset);
      fd.append("stem_denoise", denoisePreset);
      fd.append("demucs_device", devicePreset);
      fetchInit = { method: "POST", body: fd };
    } else {
      fetchInit = {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: urlInput.value,
          // Backend uses this to decide whether to ffmpeg-amix a
          // "selected stems" track (mix.wav) at the end of the pipeline.
          stems: stemSel,
          quality_preset: preset,
          stem_denoise: denoisePreset,
          demucs_device: devicePreset,
        }),
      };
    }

    let jobId;
    try {
      const res = await fetch("/api/jobs", fetchInit);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      jobId = data.job_id;
    } catch (err) {
      if (foreground) {
        setWaveformLoading(false);
        if (file) jobBox.classList.add("hidden");
      }
      showError(`Failed to start job: ${err.message}`);
      setSubmitProcessing(false);
      return;
    }

    jobSources.set(jobId, sourceUrl);
    activeJobIds.add(jobId);
    addTrackToLibrary({
      id: jobId,
      title: displayTitle,
      channel: "Queued",
      thumb: "",
      stems: stemSel,
      selectedStems: stemSel,
      qualityPreset: preset,
      stemDenoisePreset: denoisePreset,
      demucsDevice: devicePreset,
      demucsDeviceResolved: "",
      audioStems: [],
      status: "queued",
      progressPercent: 0,
      queuePosition: null,
      queueSize: 0,
      bpm: null,
      key: null,
      scale: null,
      keyConfidence: null,
      lufs: null,
      peakDb: null,
      bassRepairApplied: false,
      phaseRepairApplied: false,
      phaseRepairResidualRatio: null,
      stemDenoiseApplied: false,
      stemGateApplied: false,
      stemGateThresholdDb: null,
      processingStartedAt: null,
      timerStartedAt: null,
      processingSeconds: null,
      completedAt: null,
      sourceUrl,
    });

    showJobMonitor(jobId, "queued", { ownStudio: foreground });
    syncActiveJobs();
    setSubmitProcessing(false);
  });
}
