const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function focusableElements(dialog) {
  return [...dialog.querySelectorAll(FOCUSABLE_SELECTOR)].filter((element) => {
    if (!(element instanceof HTMLElement)) return false;
    if (element.getAttribute("aria-disabled") === "true") return false;
    return element.getClientRects().length > 0;
  });
}

export function createModalController({
  dialog,
  trigger = null,
  closeButton = null,
  initialFocus = null,
  bindTrigger = true,
  onOpen = null,
  onClose = null,
}) {
  if (!dialog) return null;

  let returnFocus = null;

  function isOpen() {
    return !dialog.classList.contains("hidden");
  }

  function open() {
    if (isOpen()) return;
    returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.classList.remove("hidden");
    dialog.setAttribute("aria-hidden", "false");
    trigger?.setAttribute("aria-expanded", "true");
    document.querySelector(".app")?.setAttribute("inert", "");
    onOpen?.();
    queueMicrotask(() => {
      const target = initialFocus || closeButton || focusableElements(dialog)[0];
      target?.focus();
    });
  }

  function close() {
    if (!isOpen()) return;
    dialog.classList.add("hidden");
    dialog.setAttribute("aria-hidden", "true");
    trigger?.setAttribute("aria-expanded", "false");
    document.querySelector(".app")?.removeAttribute("inert");
    onClose?.();
    const target = returnFocus || trigger;
    returnFocus = null;
    target?.focus?.();
  }

  if (bindTrigger) trigger?.addEventListener("click", open);
  closeButton?.addEventListener("click", close);
  dialog.addEventListener("mousedown", (event) => {
    if (event.target === dialog) close();
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = focusableElements(dialog);
    if (focusable.length === 0) {
      event.preventDefault();
      dialog.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  return Object.freeze({ open, close, isOpen });
}
