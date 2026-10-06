import assert from "node:assert/strict";
import test from "node:test";
import { runtimeNeedsUpdate } from "../desktop/ui/runtime.js";

const manifest = { version: "0.7.0-alpha.17", runtimeSha256: "a".repeat(64) };
const current = { manifest, installedVersion: manifest.version, installedSha256: manifest.runtimeSha256 };

test("matching version and archive content reuse the installed runtime", () => {
  assert.equal(runtimeNeedsUpdate(current), false);
});

test("a rebuilt archive updates even when its version is unchanged", () => {
  assert.equal(runtimeNeedsUpdate({ ...current, installedSha256: "b".repeat(64) }), true);
});

test("legacy installations without a recorded checksum are refreshed", () => {
  assert.equal(runtimeNeedsUpdate({ ...current, installedSha256: undefined }), true);
});

test("an older version is updated", () => {
  assert.equal(runtimeNeedsUpdate({ ...current, installedVersion: "0.7.0-alpha.16" }), true);
});

test("a missing manifest does not invent an update", () => {
  assert.equal(runtimeNeedsUpdate({ installedVersion: manifest.version }), false);
});
