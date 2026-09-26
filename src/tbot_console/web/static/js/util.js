export function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[c],
  );
}

/**
 * Строит корневой элемент из HTML-строки. Вызывается только с разметкой,
 * дающей один корневой элемент, поэтому возвращаемый тип сужен до HTMLElement.
 * @param {string} html
 * @returns {HTMLElement}
 */
export function el(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return /** @type {HTMLElement} */ (template.content.firstElementChild);
}

const dtf = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

const dtfFull = new Intl.DateTimeFormat("ru-RU", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

export function fmtTime(ts) {
  if (!ts) return "—";
  return dtf.format(new Date(ts * 1000));
}

export function fmtTimeFull(ts) {
  if (!ts) return "—";
  return dtfFull.format(new Date(ts * 1000));
}

export function fmtDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}ч ${m}м`;
  if (m > 0) return `${m}м ${s}с`;
  return `${s}с`;
}

export function fmtCount(value) {
  if (!Number.isFinite(value)) return "—";
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)} млн`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)} тыс`;
  return String(Math.round(value));
}

export function fmtNum(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (abs !== 0 && abs < 0.0001) return value.toExponential(2);
  return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

export function fmtPrice(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value)
    .toLocaleString("ru-RU", { maximumFractionDigits: digits })
    .replace(/[\s\u00a0\u202f]/g, "'");
}

export function fmtUsd(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value < 0 ? "−" : "";
  const abs = Math.abs(value);
  if (abs >= 10_000_000) {
    return `${sign}$${abs.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 2 })}`;
  }
  return `${sign}$${abs.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function pnlClass(value) {
  if (!value) return "";
  return value > 0 ? "pos" : "neg";
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function downloadCsv(filename, rows) {
  const text = rows
    .map((row) =>
      row
        .map((cell) => {
          const s = String(cell ?? "");
          return /[",;\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        })
        .join(";"),
    )
    .join("\n");
  const blob = new Blob([`﻿${text}`], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
