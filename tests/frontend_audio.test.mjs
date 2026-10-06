import assert from "node:assert/strict";
import { setImmediate as nextTick } from "node:timers/promises";
import test from "node:test";

class Node {
  gain = { setTargetAtTime() {} };
  connect() {}
  disconnect() {}
  start() {}
  stop() {}
}

class AudioContext {
  currentTime = 0;
  state = "running";
  destination = {};
  createGain() { return new Node(); }
  createAnalyser() { return new Node(); }
  createBufferSource() { return new Node(); }
  async decodeAudioData() { return { duration: 30 }; }
  async close() { this.state = "closed"; }
}

globalThis.window = { AudioContext };
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};
const { createAudioEngine } = await import("../static/js/audioEngine.js");
const stems = Array.from({ length: 6 }, (_, index) => ({ name: `stem${index}`, url: `/stem${index}.wav` }));
const response = () => ({ ok: true, async arrayBuffer() { return new ArrayBuffer(8); } });

test("switching tracks aborts audio requests and clears the playback clock", async () => {
  const requests = [];
  globalThis.fetch = (url, options) => new Promise((resolve) => requests.push({ options, resolve }));
  const engine = createAudioEngine(stems);
  engine.destroy();
  const aborted = requests.every(({ options }) => options?.signal?.aborted);
  for (const request of requests) request.resolve(response());
  assert.equal(await engine.ready, false);
  assert.equal(aborted, true);
  assert.equal(engine.isPlaying(), false);
  assert.equal(engine.getDuration(), 0);
});

test("decoding does not start all six downloads at once", async () => {
  let active = 0;
  let maximum = 0;
  globalThis.fetch = async () => {
    active += 1;
    maximum = Math.max(maximum, active);
    await nextTick();
    active -= 1;
    return response();
  };
  const engine = createAudioEngine(stems);
  assert.equal(await engine.ready, true);
  assert.equal(engine.getBuffers().size, stems.length);
  assert.ok(maximum <= 2, `observed ${maximum} simultaneous downloads`);
  engine.destroy();
});

test("a missing stem does not silently play an incomplete mix", async () => {
  globalThis.fetch = async (url) => url === stems[1].url ? { ok: false, status: 404 } : response();
  const engine = createAudioEngine(stems);
  assert.equal(await engine.ready, false);
  assert.equal(engine.getBuffers().size, 0);
  engine.destroy();
});
