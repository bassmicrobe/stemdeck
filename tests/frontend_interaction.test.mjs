import assert from "node:assert/strict";
import test from "node:test";

const { playbackShortcutAllowed, wireSeekSlider } = await import("../static/js/interaction.js");

test("playback shortcuts do not interfere with buttons, forms, dialogs or modifier shortcuts", () => {
  const passiveTarget = { closest() { return null; } };
  assert.equal(playbackShortcutAllowed({ target: passiveTarget }), true);
  for (const flag of ["defaultPrevented", "ctrlKey", "metaKey", "altKey", "isComposing", "repeat"]) {
    assert.equal(playbackShortcutAllowed({ target: passiveTarget, [flag]: true }), false);
  }
  assert.equal(playbackShortcutAllowed({ target: { closest() { return {}; } } }), false);
  assert.equal(playbackShortcutAllowed({ target: passiveTarget }, true), false);
});

test("touch seeking clamps the position and ends on pointer cancellation", () => {
  const listeners = new Map();
  const captures = new Set();
  const slider = {
    addEventListener(name, callback) { listeners.set(name, callback); },
    getAttribute() { return "false"; },
    getBoundingClientRect() { return { left: 20, width: 100 }; },
    setPointerCapture(id) { captures.add(id); },
    releasePointerCapture(id) { captures.delete(id); },
    hasPointerCapture(id) { return captures.has(id); },
  };
  const seeks = [];
  wireSeekSlider(slider, { getDuration: () => 200, getTime: () => 50, seek: (value) => seeks.push(value) });
  listeners.get("pointerdown")({ pointerId: 4, button: 0, clientX: 70, preventDefault() {} });
  listeners.get("pointermove")({ pointerId: 4, clientX: 150 });
  listeners.get("pointercancel")({ pointerId: 4 });
  listeners.get("pointermove")({ pointerId: 4, clientX: 30 });
  assert.deepEqual(seeks, [100, 200]);
  assert.equal(captures.size, 0);
});
