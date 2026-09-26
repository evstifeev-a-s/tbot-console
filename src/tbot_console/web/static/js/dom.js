/**
 * @param {string} tag
 * @param {Record<string, unknown> | null} [props]
 * @param {...unknown} children
 * @returns {HTMLElement}
 */
export function h(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = String(value);
    else if (key === "hidden") node.hidden = true;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), /** @type {EventListener} */ (value));
    } else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "object" ? /** @type {Node} */ (child) : String(child));
  }
  return node;
}

export function replaceChildrenIfChanged(parent, nodes) {
  const current = parent.children;
  if (current.length === nodes.length && nodes.every((node, i) => current[i] === node)) return;
  parent.replaceChildren(...nodes);
}

export function showConfirm(dialog, cancel, apply, signal) {
  if (signal?.aborted) return Promise.resolve(false);
  return new Promise((resolve) => {
    let settled = false;
    const close = (value) => {
      if (settled) return;
      settled = true;
      dialog.close();
      dialog.remove();
      resolve(value);
    };
    cancel.addEventListener("click", () => close(false));
    apply.addEventListener("click", () => close(true));
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      close(false);
    });
    signal?.addEventListener("abort", () => close(false), { once: true });
    document.body.append(dialog);
    dialog.showModal();
  });
}
