const SHOW_DELAY_MS = 90;
const HIDE_DELAY_MS = 220;
const GAP = 8;
const EDGE = 8;

export function tipPosition(anchor, tip, viewport, gap = GAP, edge = EDGE) {
  const maxLeft = Math.max(edge, viewport.width - tip.width - edge);
  const left = Math.min(Math.max(edge, anchor.left + anchor.width / 2 - tip.width / 2), maxLeft);
  const below = anchor.top + anchor.height + gap;
  const above = anchor.top - tip.height - gap;
  const fitsBelow = below + tip.height <= viewport.height - edge;
  const placeBelow = fitsBelow || above < edge;
  return { left, top: placeBelow ? below : above, placement: placeBelow ? "below" : "above" };
}

let bubble = null;
let showTimer = null;
let hideTimer = null;
let current = null;

function ensureBubble() {
  if (bubble) return bubble;
  bubble = document.createElement("div");
  bubble.className = "tip-bubble";
  bubble.setAttribute("role", "tooltip");
  bubble.hidden = true;
  bubble.addEventListener("pointerenter", () => clearTimeout(hideTimer));
  bubble.addEventListener("pointerleave", hide);
  document.body.appendChild(bubble);
  return bubble;
}

export function hide() {
  clearTimeout(showTimer);
  clearTimeout(hideTimer);
  current = null;
  if (bubble) bubble.hidden = true;
}

function scheduleHide() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(hide, HIDE_DELAY_MS);
}

function place(target) {
  const tip = ensureBubble();
  tip.textContent = target.getAttribute("data-tip") || "";
  if (!tip.textContent) {
    hide();
    return;
  }
  tip.hidden = false;
  tip.style.visibility = "hidden";
  tip.style.left = "0px";
  tip.style.top = "0px";
  const rect = target.getBoundingClientRect();
  const size = tip.getBoundingClientRect();
  const spot = tipPosition(
    rect,
    { width: size.width, height: size.height },
    { width: window.innerWidth, height: window.innerHeight },
  );
  tip.style.left = `${Math.round(spot.left)}px`;
  tip.style.top = `${Math.round(spot.top)}px`;
  tip.dataset.placement = spot.placement;
  tip.style.visibility = "visible";
}

function onEnter(event) {
  const target = /** @type {HTMLElement} */ (event.target)?.closest?.("[data-tip]");
  if (!target) return;
  clearTimeout(hideTimer);
  if (target === current) return;
  current = target;
  clearTimeout(showTimer);
  showTimer = setTimeout(() => place(target), SHOW_DELAY_MS);
}

function onLeave(event) {
  const target = /** @type {HTMLElement} */ (event.target)?.closest?.("[data-tip]");
  if (target && target === current) scheduleHide();
}

function followAnchor() {
  if (!current) return;
  const rect = current.getBoundingClientRect();
  const offscreen =
    rect.bottom < 0 ||
    rect.top > window.innerHeight ||
    rect.right < 0 ||
    rect.left > window.innerWidth;
  if (offscreen) {
    hide();
    return;
  }
  if (bubble && !bubble.hidden) place(current);
}

export function initTooltips(root = document) {
  root.addEventListener("pointerover", onEnter, true);
  root.addEventListener("pointerout", onLeave, true);
  root.addEventListener("focusin", onEnter, true);
  root.addEventListener("focusout", onLeave, true);
  window.addEventListener("scroll", followAnchor, true);
  window.addEventListener("resize", followAnchor);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hide();
  });
}
