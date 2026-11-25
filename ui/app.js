import { $, api, badge, busyButton, button, durationParts, el, formatDate, icon, policyBadge, showMessage } from "./common.js";
import { experimentView } from "./experiments.js";
import { manualIndex } from "./manuals.js";

document.querySelectorAll("[data-icon]").forEach((placeholder) => placeholder.replaceWith(icon(placeholder.dataset.icon)));

let catalog = null, history = [], selected = null, traceRevision = 0, historyRevision = 0;
let asking = false, historyError = "", historySelection = 0;
const pendingVotes = new Set(), pendingSamples = new Set(), linked = new Set(), localVotes = new Map();
const input = $("question"), form = $("ask-form"), answer = $("answer-body"), historyList = $("history-list");
const manuals = manualIndex((question) => {
  input.value = question;
  location.hash = "workbench";
  requestAnimationFrame(() => input.focus());
});
const experiments = experimentView(() => catalog?.projectUrl);
const views = { workbench: "问答工作台", experiments: "回归实验", manuals: "手册索引" };

function navigate() {
  const name = location.hash.slice(1);
  if (name === "main") return;
  const active = Object.hasOwn(views, name) ? name : "workbench";
  for (const section of Object.keys(views)) $(section).hidden = section !== active;
  for (const link of document.querySelectorAll("a[data-view]")) {
    if (link.dataset.view === active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  $("crumb").textContent = views[active];
  document.title = views[active] + " · Langfuse Lab";
  window.scrollTo(0, 0);
  if (active === "experiments") experiments.load();
}

function fillQuestion(question) {
  input.value = question;
  input.focus();
  showMessage($("ask-error"));
}

function connectionState(online) {
  const status = $("connection");
  status.className = "connection " + (online ? "online" : "offline");
  if (online) status.lastElementChild.textContent = "Langfuse 已连接";
  else status.lastElementChild.replaceChildren(button("text-button", "Langfuse 离线 · 重试", loadCatalog));
}

async function loadCatalog() {
  const status = $("connection");
  try {
    catalog = await api("catalog");
    $("console-link").href = catalog.projectUrl;
    $("model-name").textContent = catalog.model;
    $("dataset-name").textContent = catalog.dataset;
    connectionState(catalog.connected);
    $("quick-questions").replaceChildren(...catalog.cases.map(({ question }) =>
      button("chip", question, () => fillQuestion(question))));
    if (!input.value && catalog.cases.length) input.value = catalog.cases[0].question;
    manuals.load(catalog);
    if (selected) renderSelection();
  } catch (error) {
    status.className = "connection offline";
    status.lastElementChild.replaceChildren(button("text-button", "连接失败 · 重试", loadCatalog));
    $("model-name").textContent = "未连接";
    showMessage($("ask-error"), error.message, true);
  }
}

function lookup(id) {
  return catalog?.documents.find((doc) => doc.id === id);
}

function renderAnswer(text) {
  const content = el("p", "answer-text");
  for (const part of text.split(/(\[[A-Z][A-Z0-9-]*\])/g)) {
    const id = part.slice(1, -1);
    if (part.startsWith("[") && lookup(id)) {
      const citation = button("citation", part, () => manuals.open(id));
      citation.setAttribute("aria-label", "查看引用手册 " + id);
      content.append(citation);
    } else content.append(part);
  }
  return content;
}

function treeNode(root, name, glyph, ms) {
  const measure = durationParts(ms);
  return el("div", "tree-node " + (root ? "tree-root" : "tree-child"),
    el("span", "tree-icon", icon(glyph)), el("span", "", name),
    el("span", "node-time", measure.join(" ").trim()));
}

function renderTrace(row) {
  const tree = $("trace-tree"), link = $("trace-link");
  link.hidden = !row?.url;
  if (row?.url) link.href = row.url;
  const parts = durationParts(row?.duration);
  $("duration").replaceChildren(parts[0], el("small", "", parts[1]));
  $("tokens").textContent = row?.usage?.total?.toLocaleString("zh-CN") ?? "—";
  $("tokens").title = row?.usage?.input != null && row?.usage?.output != null ?
    "输入 " + row.usage.input + " · 输出 " + row.usage.output : "";
  if (!row) {
    tree.replaceChildren(el("p", "dark-muted", "运行问答后可查看调用步骤。"));
    return;
  }
  const children = [treeNode(true, "workorder-answer", "git-branch", row.duration),
    treeNode(false, "lookup-manual", "search", row.lookupMs)];
  if (row.source) children.push(treeNode(false, "glm-answer", "cpu", row.generationMs));
  tree.replaceChildren(...children);
  if (row.detailMessage) {
    const retry = button("trace-link", "重新读取", () => selectTrace(row));
    tree.append(el("p", "sync-note", row.detailMessage, " ", retry));
  } else if (!row.synced) tree.append(el("p", "sync-note", "正在读取 Langfuse 追踪…"));
}

function renderSource(row) {
  const body = $("source-body");
  const doc = row?.source ? { ...lookup(row.source), ...row.document } : null;
  $("view-source").hidden = !lookup(row?.source);
  if (!doc) {
    const message = row ? "检索没有命中手册，本次问答跳过了模型生成。" : "运行问答后可查看检索依据。";
    body.replaceChildren(el("p", "empty", message));
    return;
  }
  body.replaceChildren(el("div", "source-meta", el("span", "code", row.source), badge(doc.status)),
    el("h3", "", doc.title || row.source), el("p", "excerpt", doc.text || "尚无文档正文。"));
}

function renderControls() {
  $("answer-tools").hidden = !selected;
  if (!selected) return;
  const { id, question } = selected;
  for (const vote of ["up", "down"]) {
    const control = $("vote-" + vote);
    control.setAttribute("aria-pressed", String(selected.vote === vote));
    control.disabled = !selected.synced || pendingVotes.has(id);
  }
  const sample = $("link-sample");
  sample.hidden = !catalog?.cases.some((item) => item.question === question);
  sample.disabled = !selected.synced || linked.has(id) || pendingSamples.has(id);
  sample.lastElementChild.textContent = linked.has(id) ? "已关联样本" : "关联回归样本";
}

function renderSelection() {
  $("answer-version").textContent = selected?.promptVersion != null ? "Prompt v" + selected.promptVersion : "";
  if (selected) {
    answer.replaceChildren(el("p", "answer-question", icon("message-square"), selected.question, policyBadge(selected.policy)),
      renderAnswer(selected.answer || "正在读取回答…"));
  } else answer.replaceChildren(el("p", "empty", "运行一次问答，回答和引用会显示在这里。"));
  renderControls();
  renderTrace(selected);
  renderSource(selected);
}

async function selectTrace(row) {
  const request = ++traceRevision;
  selected = { ...row, detailMessage: "" };
  showMessage($("action-status"));
  renderSelection();
  highlightHistory();
  // Langfuse ingests SDK writes asynchronously. Keep the returned answer visible while metrics arrive.
  const waits = [0, 450, 900, 1600, 3000, 5000, 8000];
  for (const wait of waits) {
    if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
    if (request !== traceRevision) return;
    try {
      const detail = await api("traces/" + row.id);
      if (request !== traceRevision) return;
      if (localVotes.get(row.id) === detail.vote) localVotes.delete(row.id);
      selected = { ...row, ...detail, vote: localVotes.get(row.id) ?? detail.vote, detailMessage: "" };
      renderSelection();
      if (detail.synced) return;
    } catch (error) {
      if (request !== traceRevision) return;
      if (error.status === 404 && wait !== waits.at(-1)) continue;
      selected.detailMessage = error.message;
      renderTrace(selected);
      return;
    }
  }
  selected.detailMessage = "追踪仍在入库。";
  renderTrace(selected);
}

function highlightHistory() {
  historyList.querySelectorAll(".history-row").forEach((row) =>
    row.setAttribute("aria-pressed", String(row.dataset.id === selected?.id)));
}

function renderHistory() {
  const activeId = historyList.contains(document.activeElement) ? document.activeElement.dataset.id : null;
  historyList.replaceChildren();
  if (historyError) historyList.append(el("p", "inline-error", historyError));
  if (!history.length) {
    if (!historyError) historyList.append(el("p", "empty", "还没有问答记录。"));
    return;
  }
  const header = el("div", "history-head", el("span", "", "问题"), el("span", "", "检索策略"),
    el("span", "hist-source", "命中手册"), el("span", "hist-time", "开始时间"), el("span"));
  header.setAttribute("aria-hidden", "true");
  const table = el("div", "history-table", header);
  for (const item of history) {
    const row = button("history-row", el("span", "hist-question", item.question), () => {
      historySelection++;
      selectTrace(item);
      if ($("answer-title").getBoundingClientRect().top < 0) {
        $("answer-title").setAttribute("tabindex", "-1");
        $("answer-title").focus();
      }
    });
    row.dataset.id = item.id;
    row.title = item.question;
    row.append(policyBadge(item.policy), el("span", "hist-source code", item.source || "—"),
      el("span", "hist-time", formatDate(item.at)), icon("chevron-right"));
    table.append(row);
  }
  historyList.append(table);
  highlightHistory();
  if (activeId) [...table.querySelectorAll(".history-row")].find((row) => row.dataset.id === activeId)?.focus({ preventScroll: true });
}

async function loadHistory(initial = false) {
  const request = ++historyRevision;
  busyButton($("refresh-history"), true, "读取中", "rotate-cw", true);
  try {
    const rows = await api("history");
    if (request !== historyRevision) return;
    if (catalog) connectionState(true);
    // A just-finished request can take a moment to appear in the history query.
    history = [...history.filter((row) => row.fresh && !rows.some((saved) => saved.id === row.id)), ...rows].slice(0, 12);
    historyError = "";
    renderHistory();
    if (!selected && history.length) selectTrace(history[0]);
    else if (!selected) renderSelection();
  } catch (error) {
    if (request !== historyRevision) return;
    historyError = error.message;
    renderHistory();
    if (initial && !selected) {
      renderSelection();
      setTimeout(() => { if (request === historyRevision) loadHistory(); }, 1800);
    }
  } finally {
    if (request === historyRevision) busyButton($("refresh-history"), false, "刷新", "rotate-cw", true);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (asking) return;
  const question = input.value.trim();
  if (!question) {
    showMessage($("ask-error"), "请输入要查证的工单问题。", true);
    input.focus();
    return;
  }
  const policy = form.elements.policy.value;
  const selectionAtStart = historySelection;
  asking = true;
  busyButton($("ask-button"), true, "正在运行", "arrow-right");
  showMessage($("ask-error"));
  try {
    const row = await api("ask", { question, policy });
    history = [{ ...row, fresh: true }, ...history.filter((old) => old.id !== row.id)].slice(0, 12);
    renderHistory();
    if (selectionAtStart === historySelection || !selected) selectTrace(row);
    else {
      const message = $("action-status");
      showMessage(message, "新问答已完成。");
      message.append(" ", button("text-button", "查看回答", () => selectTrace(row)));
    }
  } catch (error) {
    showMessage($("ask-error"), error.message, true);
  } finally {
    asking = false;
    busyButton($("ask-button"), false, "运行问答", "arrow-right");
  }
});

input.addEventListener("keydown", (event) => {
  if (!event.isComposing && (event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    form.requestSubmit();
  }
});
for (const vote of ["up", "down"]) {
  $("vote-" + vote).addEventListener("click", async () => {
    const id = selected?.id;
    if (!id || pendingVotes.has(id)) return;
    pendingVotes.add(id);
    renderControls();
    try {
      const stored = await api("traces/" + id + "/feedback", { vote });
      localVotes.set(id, stored.vote);
      if (selected?.id === id) {
        selected.vote = stored.vote;
        showMessage($("action-status"), "反馈已提交到 Langfuse。");
      }
    } catch (error) {
      if (selected?.id === id) showMessage($("action-status"), error.message, true);
    } finally {
      pendingVotes.delete(id);
      renderControls();
    }
  });
}
$("link-sample").addEventListener("click", async () => {
  const id = selected?.id;
  if (!id || pendingSamples.has(id)) return;
  pendingSamples.add(id);
  renderControls();
  try {
    await api("traces/" + id + "/sample", {});
    linked.add(id);
    if (selected?.id === id) showMessage($("action-status"), "这条 Trace 已关联到数据集中的对应样本。");
  } catch (error) {
    if (selected?.id === id) showMessage($("action-status"), error.message, true);
  } finally {
    pendingSamples.delete(id);
    renderControls();
  }
});
$("view-source").addEventListener("click", () => { if (selected?.source) manuals.open(selected.source); });
$("refresh-history").addEventListener("click", () => loadHistory());
window.addEventListener("hashchange", navigate);
navigate();
loadCatalog();
loadHistory(true);
