import { $, badge, button, el, icon } from "./common.js";

export function manualIndex(onAsk) {
  let documents = [], cases = [], filter = "all", selected = null;
  const search = $("manual-search");
  const filters = [...$("manual-filters").querySelectorAll("button")];
  const list = $("manual-list");
  const detail = $("manual-detail");

  function renderDetail(doc) {
    if (!doc) {
      detail.replaceChildren(el("p", "muted", "请选择一份手册查看原文。"));
      return;
    }
    const example = cases.find((item) => item.source === doc.id) ||
      cases.find((item) => doc.keywords.some((word) => item.question.includes(word)));
    const ask = button("primary", "用此主题提问", () => onAsk(example?.question || doc.title));
    ask.append(icon("arrow-right"));
    detail.replaceChildren(
      el("div", "document-meta", badge(doc.status), el("span", "code", doc.id)),
      el("h2", "", doc.title), el("p", "", doc.text),
      el("div", "inline-wrap", ...doc.keywords.map((word) => el("span", "keyword", word))), ask,
    );
  }

  function render() {
    const term = search.value.trim().toLocaleLowerCase();
    const visible = documents.filter((doc) =>
      (filter === "all" || doc.status === filter) &&
      [doc.title, doc.id, ...doc.keywords].join(" ").toLocaleLowerCase().includes(term));
    const active = visible.find((doc) => doc.id === selected) || visible[0];
    selected = active?.id || null;
    filters.forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.filter === filter)));
    const hadListFocus = list.contains(document.activeElement);
    list.replaceChildren(...visible.map((doc) => {
      const row = button("manual-item", icon("file-text"), () => { selected = doc.id; render(); });
      row.setAttribute("aria-pressed", String(doc === active));
      row.append(el("span", "", el("strong", "", doc.title), el("small", "", doc.id)), badge(doc.status));
      return row;
    }));
    if (hadListFocus) list.querySelector('[aria-pressed="true"]')?.focus({ preventScroll: true });
    if (!visible.length) list.append(el("p", "empty", "没有匹配的手册。"));
    renderDetail(active);
  }

  search.addEventListener("input", render);
  filters.forEach((item) => item.addEventListener("click", () => { filter = item.dataset.filter; render(); }));
  return {
    load(catalog) {
      documents = [...catalog.documents].sort((a, b) => Number(b.status === "current") - Number(a.status === "current"));
      cases = catalog.cases;
      render();
    },
    open(id) {
      selected = id;
      search.value = "";
      filter = "all";
      render();
      window.location.hash = "manuals";
    },
  };
}
