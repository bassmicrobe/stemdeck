import { $, storeGet, storeSet } from "./utils.js";
import { STEM_NAMES, supportedStemNamesForQuality } from "./constants.js";

// ─── DOM refs ───

export const form = $("job-form");
export const urlInput = $("url");
export const submitBtn = $("submit");
export const qualitySelect = $("qualityPreset");
export const denoiseSelect = $("stemDenoise");

export const playBtn = $("t-play");
export const playMiniBtn = $("t-play-mini");
export const stopBtn = $("t-stop");
export const loopBtn = $("t-loop");
export const titleEl = $("title");
export const bpmChip = $("t-bpm");
export const keyChip = $("t-key");
export const stemsChip = $("t-stems-chip");
export const timeEl = $("t-time");
export const masterFader = $("t-master");
export const npArt = $("np-art");
export const npThumb = $("np-thumb");

export const jobBox = $("job");
export const jobTitleEl = $("job-title");
export const jobStageEl = $("job-stage");
export const jobDetailEl = $("job-detail");
export const jobEtaEl = $("job-eta");
export const jobPercentEl = $("job-percent");
export const jobCancelBtn = $("job-cancel");
export const progressEl = $("progress");

export const errorEl = $("error");
export const lanesEl = $("lanes");
export const mixerEl = $("mixer");
export const multitrackContainer = $("multitrack-container");
export const wavesGrid = $("waves-grid");
export const rulerTime = $("ruler-time");
export const loopRegionEl = $("loop-region");
export const playheadMarker = document.querySelector(".playhead-marker");
export const waveScroll = $("wave-scroll");
export const waveCanvas = $("wave-canvas");
export const presenceRulerEl = $("presence-ruler");
export const presencePlayheadEl = $("presence-playhead");
export const footerTimeElapsed = $("footer-time-elapsed");
export const footerTimeTotal = $("footer-time-total");
export const stemListEl = document.querySelector(".stem-list");
export const npScrubEl = document.querySelector(".np-scrub");
export const npScrubFill     = $("footer-scrub-fill");
export const footerTitle     = $("footer-title");
export const footerMeta      = $("footer-meta");
export const footerThumb     = $("footer-thumb");

// ─── Mutable state ───

export let eventSource = null;
export let multitrack = null;
// Web Audio decode-and-mix engine (Safari-safe playback). Null = legacy streaming path.
export let audioEngine = null;
export let currentJobId = null;

// `mixerState` is mutated in place (never reassigned). renderMixerRow's
// closures capture each entry by reference, so on a new job we merge
// localStorage values into the existing objects rather than replacing them.
export const mixerState = {};

export let trackIndex = {};
export let totalDuration = 0;
export let loopEnabled = false;
export let loopStart = 0;
export let loopEnd = 0;

// Selected stems for extraction. The set determines (a) which stem
// rows render in the studio dashboard after a job completes and (b)
// which stem audio gets loaded into the multitrack. Backend always
// runs Demucs on all 6 stems regardless -- filtering happens entirely
// client-side at render time. Persisted across reloads in localStorage
// so a user who turns off "Vocals" stays set up that way for the
// next song.
const _STEM_SEL_KEY = "stemdeck:selected-stems";
const _QUALITY_PRESET_KEY = "stemdeck:quality-preset";
const _STEM_DENOISE_KEY = "stemdeck:stem-denoise";
const QUALITY_PRESETS = new Set(["standard", "high", "max"]);
const STEM_DENOISE_PRESETS = new Set(["off", "light", "strong"]);

// Start with all stems selected (safe default). The async store load below
// updates this binding once the store is available; ES module live bindings
// ensure all importers see the updated value on next read.
export let selectedStems = new Set(STEM_NAMES);

// Resolves when the persisted stem selection has been loaded from the store.
// Consumers that need the exact stored selection (e.g. the stem-choice UI)
// should await this before reading selectedStems.
export const stemSelectionReady = (async () => {
  try {
    const arr = await storeGet(_STEM_SEL_KEY, null);
    if (Array.isArray(arr) && arr.length > 0) {
      const valid = arr.filter((n) => STEM_NAMES.includes(n));
      if (valid.length > 0) {
        selectedStems = new Set(valid);
        return;
      }
    }
  } catch (e) { console.warn("[state] failed to load stem selection:", e); }
  // Keep the all-stems default.
})();

export let qualityPreset = "standard";
export let stemDenoisePreset = "off";

export const qualityPresetReady = (async () => {
  try {
    const stored = await storeGet(_QUALITY_PRESET_KEY, null);
    if (typeof stored === "string" && QUALITY_PRESETS.has(stored)) {
      qualityPreset = stored;
    }
  } catch (e) { console.warn("[state] failed to load quality preset:", e); }
})();

export const stemDenoiseReady = (async () => {
  try {
    const stored = await storeGet(_STEM_DENOISE_KEY, null);
    if (typeof stored === "string" && STEM_DENOISE_PRESETS.has(stored)) {
      stemDenoisePreset = stored;
    }
  } catch (e) { console.warn("[state] failed to load stem denoise preset:", e); }
})();

export function setQualityPreset(value) {
  qualityPreset = QUALITY_PRESETS.has(value) ? value : "standard";
  storeSet(_QUALITY_PRESET_KEY, qualityPreset).catch((e) =>
    console.warn("[state] failed to save quality preset:", e)
  );
}

export function setStemDenoisePreset(value) {
  stemDenoisePreset = STEM_DENOISE_PRESETS.has(value) ? value : "off";
  storeSet(_STEM_DENOISE_KEY, stemDenoisePreset).catch((e) =>
    console.warn("[state] failed to save stem denoise preset:", e)
  );
}

export function effectiveSelectedStems(preset = qualityPreset) {
  const allowed = supportedStemNamesForQuality(preset);
  const selected = [...selectedStems].filter((name) => allowed.includes(name));
  return selected.length > 0 ? selected : [...allowed];
}

export function saveSelectedStems() {
  storeSet(_STEM_SEL_KEY, [...selectedStems]).catch((e) =>
    console.warn("[state] failed to save stem selection:", e)
  );
}
export function setStemSelected(name, selected) {
  if (selected) selectedStems.add(name);
  else selectedStems.delete(name);
  saveSelectedStems();
}

// Web Audio analysers for live VU meters.
export let audioContext = null;
export let masterVolume = 0.5; // mirrored from masterFader.value
export const trackAnalysers = []; // index → { analyser, data, vuEl }
export let vuRafId = null;

// Master bus nodes — created once in audio.js, shared across mixer.js.
// masterBusGain is driven by the master fader; masterLimiter is a
// transparent brickwall limiter that prevents inter-stem summing clipping.
export let masterBusGain = null;
export let masterLimiter = null;

// ─── Setter helpers for mutable state (so other modules can update) ───

export function setEventSource(v) { eventSource = v; }
export function setMultitrack(v) { multitrack = v; }
export function setAudioEngine(v) { audioEngine = v; }
export function setCurrentJobId(v) { currentJobId = v; }
export function setTrackIndex(v) { trackIndex = v; }
export function setTotalDuration(v) { totalDuration = v; }
export function setLoopEnabled(v) { loopEnabled = v; }
export function setLoopStart(v) { loopStart = v; }
export function setLoopEnd(v) { loopEnd = v; }
export function setAudioContext(v) { audioContext = v; }
export function setMasterVolume(v) { masterVolume = v; }
export function setVuRafId(v) { vuRafId = v; }
export function setMasterBusGain(v) { masterBusGain = v; }
export function setMasterLimiter(v) { masterLimiter = v; }

// Footer waveform draw callback — set by player.js, called by transport.js
export let footerWaveDrawFn = null;
export function setFooterWaveDrawFn(fn) { footerWaveDrawFn = fn; }
