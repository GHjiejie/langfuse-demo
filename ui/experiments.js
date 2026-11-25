import { $, api, busyButton, button, el, external, formatDate, icon, policyBadge, policyNames, showMessage } from "./common.js";

const metrics = { "retrieval-correct": "检索", "citation-correct": "引用", "steps-correct": "步骤" };
const policies = ["baseline", "current"];
const passes = (row) => Object.keys(metrics).every((key) => row?.scores?.[key] === 1);

function summary(policy, items) {
  const count = items.filter(passes).length;
  const progress = el("progress");
  progress.max = items.length || 1;
  progress.value = count;
  progress.setAttribute("aria-label", policyNames[policy] + "通过样本数");
  return el("article", "card summary-card " + policy,
    el("div", "card-head", el("h2", "", policyNames[policy]), el("span", "mono", policy)),
    el("div", "pass-count", el("b", "", count + "/" + items.length), el("small", "", "样本通过")), progress);
}

function mark(text, value) {
  const status = (value == null) ? "待评分" : value === 1 ? "通过" : "未通过";
  const item = el("span", "metric " + ((value == null) ? "unknown" : value === 1 ? "" : "fail"),
    icon(value == null ? "clock-3" : value === 1 ? "check" : "x"), text);
  item.title = text + " · " + status;
  item.setAttribute("aria-label", item.title);
  return item;
}

function samplePolicy(policy, row, projectUrl) {
  const box = el("div", "sample-result", policyBadge(policy));
  if (!row) {
    box.append(el("p", "muted", "这组还没有此样本的结果。"));
    return box;
  }
  box.append(el("p", "", row.answer || "尚无回答正文。"),
    el("div", "sample-metrics", ...Object.entries(metrics).map(([key, label]) => mark(label, row.scores[key]))));
  if (row.traceId && projectUrl) box.append(external(projectUrl + "/traces/" + row.traceId, "Trace"));
  return box;
}

function sampleRows(pair, projectUrl) {
  const samples = new Map();
  for (const policy of policies) {
    for (const item of pair.runs[policy].items) {
      const key = item.id;
      if (!samples.has(key)) samples.set(key, {});
      samples.get(key)[policy] = item;
    }
  }
  const differs = (rows) => passes(rows.baseline) !== passes(rows.current);
  const sorted = [...samples.values()].sort((a, b) => Number(differs(b)) - Number(differs(a)));
  const traceBase = projectUrl || pair.url.split("/experiments/")[0];
  return sorted.map((rows) => {
    const item = rows.current || rows.baseline;
    const labels = policies.map((policy) => {
      const scores = Object.values(rows[policy]?.scores || {});
      return mark(policyNames[policy], !scores.length || scores.some((s) => s == null) ? null : Number(passes(rows[policy])));
    });
    const toggle = el("summary", "",
      el("div", "", el("strong", "", item.question), el("small", "", "参考手册 ", el("span", "code", item.expected || "—"))),
      el("span", "sample-metrics", ...labels), icon("chevron-down"));
    return el("details", "sample", toggle, el("div", "sample-grid", ...policies.map((p) => samplePolicy(p, rows[p], traceBase))));
  });
}

export function experimentView(getProjectUrl) {
  const runButton = $("run-experiment"), content = $("experiment-content"), notice = $("exp-status");
  let pair = null, job = { state: "idle", completed: 0 };
  let timer = null, loading = false, starting = false, revision = 0;

  function renderPair() {
    if (!pair) {
      content.replaceChildren(el("div", "card", "还没有成对的实验结果。运行一次回归即可查看两种策略的差异。"));
      return;
    }
    const rows = sampleRows(pair, getProjectUrl());
    const details = el("section", "card", el("div", "card-head results-head",
      el("div", "card-title", icon("list-tree"), el("h2", "", "逐题对照")), external(pair.url, "在 Langfuse 中比较")),
      el("p", "comparison-meta", formatDate(pair.at, true) + " · 检索、引用和步骤三项全部正确时，该样本通过。"),
      ...rows);
    if (rows.length) rows[0].open = true;
    content.replaceChildren(el("div", "summary-grid", ...policies.map((p) => summary(p, pair.runs[p].items))), details);
  }

  function setPair(newPair) {
    const incomplete = pair && policies.some((p) => !pair.runs[p].items.length ||
      pair.runs[p].items.some((item) => Object.values(item.scores).some((score) => score == null)));
    if (newPair && (!pair || newPair.stamp > pair.stamp || (incomplete && newPair.stamp === pair.stamp))) {
      pair = newPair;
      renderPair();
    } else if (!pair) renderPair();
  }

  function updateJob(next) {
    job = next;
    const running = job.state === "running";
    busyButton(runButton, running, running ? "回归运行中" : "运行回归", "play", true);
    if (running) showMessage(notice, "正在运行回归实验，已完成 " + job.completed + "/2 组策略。");
    else if (job.state === "error") showMessage(notice, job.message, true);
    else showMessage(notice, job.state === "done" ? "两组策略已运行完成。" : "");
  }

  function schedulePoll() {
    window.clearTimeout(timer);
    timer = window.setTimeout(poll, 1200);
  }

  async function poll() {
    try {
      const data = await api("experiments/job");
      updateJob(data.job);
      setPair(data.pair);
      if (job.state === "running") schedulePoll();
    } catch (error) {
      showMessage(notice, error.message, true);
      const retry = button("text-button", "重新读取进度", () => { retry.disabled = true; poll(); });
      notice.append(" ", retry);
    }
  }

  async function load() {
    if (loading || starting) return;
    const request = ++revision;
    loading = true;
    if (!pair) content.replaceChildren(el("p", "muted", "正在读取最近一组实验…"));
    try {
      const data = await api("experiments");
      if (request !== revision) return;
      setPair(data.pair);
      updateJob(data.job);
      if (job.state === "running") schedulePoll();
    } catch (error) {
      if (request !== revision) return;
      if (!pair) content.replaceChildren();
      showMessage(notice, error.message, true);
      notice.append(" ", button("text-button", "重新读取", load));
    } finally {
      loading = false;
    }
  }

  runButton.addEventListener("click", async () => {
    if (starting || job.state === "running") return;
    revision++;
    starting = true;
    busyButton(runButton, true, "正在启动", "play", true);
    try {
      updateJob(await api("experiments/run", {}));
      schedulePoll();
    } catch (error) {
      updateJob(job);
      showMessage(notice, error.message, true);
    } finally {
      starting = false;
    }
  });
  return { load };
}
