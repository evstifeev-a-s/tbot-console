export const PROCESSES_PAGE = "processes";

export function unitHref(unit, page = "", arg = "") {
  let href = `#/${unit}`;
  if (page) href += `/${page}`;
  if (page && arg) href += `/${arg}`;
  return href;
}

function legacyOwner(units, segment) {
  return units.find((unit) => unit.legacy_pages?.includes(segment)) || null;
}

export function knownSegment(units, segment) {
  return units.some((unit) => unit.id === segment) || legacyOwner(units, segment) !== null;
}

export function parseHash(hash, units) {
  const body = String(hash || "")
    .replace(/^#/, "")
    .replace(/^\/+/, "");
  if (!body) return { kind: "home" };
  const [head, ...rest] = body.split("/");
  if (units.some((unit) => unit.id === head)) {
    return { kind: "unit", unit: head, page: rest[0] || "", arg: rest.slice(1).join("/") };
  }
  const owner = legacyOwner(units, head);
  if (owner) {
    return { kind: "redirect", to: unitHref(owner.id, head, rest.join("/")) };
  }
  return { kind: "unknown", segment: head };
}

export function resolvePage(pages, page) {
  if (page === PROCESSES_PAGE) return PROCESSES_PAGE;
  const list = Array.isArray(pages) ? pages : [];
  if (page && list.some((p) => p.id === page)) return page;
  return list.length ? list[0].id : PROCESSES_PAGE;
}

export function displaySegment(segment) {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}
