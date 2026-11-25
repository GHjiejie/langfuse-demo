export const $ = (id) => document.getElementById(id);
export const policyNames = { current: "仅现行", baseline: "含归档" };

export function el(tag, className, ...children) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.append(...children.filter((child) => child !== null && child !== undefined));
  return element;
}

export function icon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", "/icons.svg#" + name);
  svg.append(use);
  return svg;
}

export function button(className, label, onClick) {
  const control = el("button", className, label);
  control.type = "button";
  control.addEventListener("click", onClick);
  return control;
}

export function external(url, label) {
  const link = el("a", "text-button", label, icon("arrow-up-right"));
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener";
  return link;
}

export function badge(kind) {
  const labels = { current: "现行", archived: "已归档" };
  return el("span", "badge " + (labels[kind] ? kind : "unknown"), labels[kind] || "未记录");
}

export function policyBadge(kind) {
  return el("span", "badge " + (policyNames[kind] ? kind : "unknown"), policyNames[kind] || "未记录");
}

export async function api(path, payload) {
  const options = payload === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  };
  let response;
  try {
    response = await fetch("/api/" + path, options);
  } catch {
    throw new Error("工作台连接中断，请检查本机服务后重试。");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || "读取失败，请稍后重试。");
    error.status = response.status;
    throw error;
  }
  return data;
}

export function showMessage(element, message = "", isError = false) {
  element.textContent = message;
  element.hidden = !message;
  element.classList.toggle("is-error", isError);
}

export function busyButton(control, busy, label, symbol, iconFirst = false) {
  const glyph = icon(busy ? "rotate-cw" : symbol);
  if (busy) glyph.classList.add("spin");
  control.replaceChildren(...(iconFirst ? [glyph, label] : [label, glyph]));
  control.disabled = busy;
  control.setAttribute("aria-busy", String(busy));
}

export function formatDate(value, full = false) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    ...(full ? { year: "numeric" } : {}), month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

export function durationParts(ms) {
  if (!Number.isFinite(ms)) return ["—", ""];
  return ms < 1000 ? [String(Math.round(ms)), "ms"] : [(ms / 1000).toFixed(2), "s"];
}
