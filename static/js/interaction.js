const INTERACTIVE_SELECTOR = [
  "input", "textarea", "select", "button", "a[href]", "[contenteditable]:not([contenteditable=false])",
  '[role="button"]', '[role="slider"]', '[role="menuitem"]',
].join(",");

export function playbackShortcutAllowed(event, modalOpen = false) {
  if (modalOpen || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey
      || event.isComposing || event.repeat) return false;
  return !event.target?.closest?.(INTERACTIVE_SELECTOR);
}

export function wireSeekSlider(slider, { getDuration, getTime, seek }) {
  if (!slider) return;
  let pointerId = null;
  const enabled = () => slider.getAttribute("aria-disabled") !== "true" && getDuration() > 0;
  function seekAt(clientX) {
    if (!enabled()) return;
    const { left, width } = slider.getBoundingClientRect();
    if (width <= 0) return;
    seek(Math.max(0, Math.min(1, (clientX - left) / width)) * getDuration());
  }
  slider.addEventListener("pointerdown", (event) => {
    if (!enabled() || event.button !== 0 || pointerId !== null) return;
    event.preventDefault();
    pointerId = event.pointerId;
    slider.setPointerCapture(pointerId);
    seekAt(event.clientX);
  });
  slider.addEventListener("pointermove", (event) => {
    if (event.pointerId === pointerId) seekAt(event.clientX);
  });
  function endSeek(event) {
    if (event.pointerId !== pointerId) return;
    const previousId = pointerId;
    pointerId = null;
    if (slider.hasPointerCapture(previousId)) slider.releasePointerCapture(previousId);
  }
  slider.addEventListener("pointerup", endSeek);
  slider.addEventListener("pointercancel", endSeek);
  slider.addEventListener("lostpointercapture", endSeek);
  slider.addEventListener("keydown", (event) => {
    if (!enabled()) return;
    const step = event.shiftKey ? 10 : 1;
    const destinations = {
      Home: 0, End: getDuration(), ArrowLeft: getTime() - step, ArrowRight: getTime() + step,
    };
    if (!Object.hasOwn(destinations, event.key)) return;
    event.preventDefault();
    seek(Math.max(0, Math.min(getDuration(), destinations[event.key])));
  });
}
