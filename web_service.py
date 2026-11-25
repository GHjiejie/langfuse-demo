"""Workorder demo operations used by the local browser interface."""

import json
import re
import threading
import time
from datetime import datetime, timezone

import httpx
from langfuse import propagate_attributes
from langfuse.api.core.api_error import ApiError

from local_setup import BASE_URL
from workorder import (DATASET, KB, MODEL, POLICIES, PROMPT, SCORE_NAMES, answer_question,
                       comparison_url, experiment_runs, model_client, record_feedback, save_case)


SESSION = "workorder-local"


class UIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def object_field(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def milliseconds(row):
    if row.get("latency") is not None:
        return round(row["latency"] * 1000)
    if row.get("startTime") and row.get("endTime"):
        start = datetime.fromisoformat(row["startTime"])
        end = datetime.fromisoformat(row["endTime"])
        return round((end - start).total_seconds() * 1000)
    return None


def trace_summary(root):
    output = object_field(root.get("output"))
    return {
        "id": root["traceId"], "observationId": root["id"], "question": root.get("input"),
        "answer": output.get("answer"), "source": output.get("retrieved_id"),
        "policy": object_field(root.get("metadata")).get("retrieval_policy"),
        "at": root.get("startTime"), "duration": milliseconds(root),
    }


def ui_message(error):
    if isinstance(error, (UIError, RuntimeError, SystemExit)):
        return str(error)
    if isinstance(error, httpx.TimeoutException):
        return "服务响应超时，请稍后重试。"
    if isinstance(error, (httpx.HTTPError, ApiError)):
        return "服务暂时无法访问，请检查本地 Langfuse 或模型连接后重试。"
    return "请求处理失败，请稍后重试。"


def result_item(item_id, question, expected, output, scores, trace_id):
    return {"id": item_id, "question": question, "expected": expected.get("source"),
            "answer": output.get("answer"), "source": output.get("retrieved_id"),
            "scores": {name: scores.get(name) for name in SCORE_NAMES}, "traceId": trace_id}


class Workbench:
    def __init__(self, client, api, project_id):
        self.client, self.api, self.project_id = client, api, project_id
        self.project_url = f"{BASE_URL}/project/{project_id}"
        self.job_lock = threading.Lock()
        self.job = {"state": "idle", "completed": 0}
        self.last_result = None

    def catalog(self):
        try:
            connected = self.api.get("projects", timeout=4).is_success
        except httpx.HTTPError:
            connected = False
        return {"model": MODEL, "connected": connected, "projectUrl": self.project_url,
                "documents": KB["documents"], "cases": KB["cases"], "dataset": DATASET}

    def data(self, path, **params):
        response = self.api.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def pages(self, path, **params):
        while True:
            page = self.data(path, limit=100, **params)
            yield from page["data"]
            cursor = page["meta"].get("cursor")
            if not cursor or cursor == params.get("cursor"):
                break
            params["cursor"] = cursor

    def history(self):
        rows = self.data("v2/observations", name="workorder-answer", sessionId=SESSION, limit=12,
                         fields="core,basic,time,io,metadata")["data"]
        return [row for row in map(trace_summary, rows) if row["answer"]]

    def trace(self, trace_id):
        rows = self.data("v2/observations", traceId=trace_id,
                         fields="core,basic,time,io,metadata,model,prompt,usage,metrics")["data"]
        by_name = {row["name"]: row for row in rows}
        root = by_name.get("workorder-answer")
        if not root or root.get("sessionId") != SESSION:
            raise UIError(404, "这条问答的追踪暂未就绪。")
        result = trace_summary(root)
        lookup = by_name.get("lookup-manual", {})
        generation = by_name.get("glm-answer", {})
        votes = self.data("v3/scores", traceId=trace_id, name="user-helpful", limit=1)["data"]
        return {**result, "url": self.trace_url(trace_id),
                "document": object_field(lookup.get("output")) or None,
                "lookupMs": milliseconds(lookup), "generationMs": milliseconds(generation),
                "usage": generation.get("usageDetails") or {}, "model": generation.get("model"),
                "promptVersion": generation.get("promptVersion"),
                "synced": bool(lookup and (generation or not result["source"])),
                "vote": ("up" if votes[0]["value"] else "down") if votes else None}

    def trace_url(self, trace_id):
        return self.project_url + "/traces/" + trace_id

    def ask(self, body):
        question, policy = body.get("question"), body.get("policy")
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
            raise UIError(400, "请输入 1 到 2000 个字符的问题。")
        if policy not in POLICIES:
            raise UIError(400, "请选择有效的检索策略。")
        question = question.strip()
        prompt = self.client.get_prompt(PROMPT, label="production")
        now = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        with model_client() as provider, propagate_attributes(session_id=SESSION, tags=[policy]):
            output, trace_id, observation_id = answer_question(self.client, provider, prompt, question, policy)
        duration = round((time.monotonic() - started) * 1000)
        self.client.flush()
        source = output["retrieved_id"]
        return {"id": trace_id, "observationId": observation_id, "question": question, "policy": policy,
                "answer": output["answer"], "source": source, "at": now, "duration": duration,
                "url": self.trace_url(trace_id), "usage": {}, "vote": None, "synced": False,
                "document": next((d for d in KB["documents"] if d["id"] == source), None),
                "model": MODEL if source else None, "promptVersion": prompt.version if source else None}

    def vote(self, trace_id, body):
        vote = body.get("vote")
        if vote not in ("up", "down"):
            raise UIError(400, "请选择有帮助或无帮助。")
        roots = self.data("v2/observations", traceId=trace_id, sessionId=SESSION,
                          name="workorder-answer", fields="core", limit=1)["data"]
        if not roots:
            raise UIError(404, "找不到这条工作台问答。")
        record_feedback(self.client, trace_id, vote)
        self.client.flush()
        return {"vote": vote}

    def link_sample(self, trace_id):
        trace = self.trace(trace_id)
        case = next((c for c in KB["cases"] if c["question"] == trace["question"]), None)
        if not case:
            raise UIError(400, "这道题尚未配置回归参考答案。")
        item = save_case(self.client, case, trace_id, trace["observationId"])
        return {"id": item.id}

    def latest_pair(self):
        dataset = self.client.api.datasets.get(DATASET)
        since = dataset.created_at.isoformat()
        groups = {}
        for run in self.pages("experiments", datasetId=dataset.id, fromStartTime=since):
            match = re.fullmatch(r"(baseline|current)-(\d{8}-\d{6}-\d{6})", run["name"])
            if not match:
                continue
            policy, stamp = match.groups()
            group = groups.setdefault(stamp, {})
            group[policy] = run
            if len(group) == len(POLICIES):
                break
        else:
            return None
        runs = {}
        for policy in POLICIES:
            run = group[policy]
            items = []
            for row in self.pages("experiment-items", experimentId=run["id"], fromStartTime=since,
                                  fields="core,dataset,io,scores"):
                items.append(result_item(
                    row["experimentItemId"], object_field(row["input"]).get("question"),
                    object_field(row["expectedOutput"]), object_field(row["output"]),
                    {s["name"]: s["value"] for s in row.get("scores", [])}, row["traceId"]))
            runs[policy] = {"id": run["id"], "items": items}
        return self.pair(stamp, group["current"]["endTime"], runs)

    def pair(self, stamp, at, runs):
        return {"stamp": stamp, "at": at, "runs": runs,
                "url": comparison_url(self.project_id, {p: r["id"] for p, r in runs.items()})}

    def experiments(self):
        stored = self.latest_pair()
        with self.job_lock:
            live = self.last_result
            pair = live if live and (not stored or live["stamp"] >= stored["stamp"]) else stored
            return {"pair": pair, "job": self.job}

    def start_experiment(self):
        with self.job_lock:
            if self.job["state"] == "running":
                return self.job
            self.job = {"state": "running", "completed": 0}
            threading.Thread(target=self.run_experiment, name="workorder-experiment", daemon=True).start()
            return self.job

    def job_state(self):
        with self.job_lock:
            return {"job": self.job, "pair": self.last_result}

    def run_experiment(self):
        try:
            prompt = self.client.get_prompt(PROMPT, label="production")
            runs = {}
            with model_client() as provider:
                for policy, run in experiment_runs(self.client, provider, prompt):
                    items = [result_item(row.item.id, row.item.input["question"], row.item.expected_output,
                                         row.output, {s.name: s.value for s in row.evaluations}, row.trace_id)
                             for row in run.item_results]
                    runs[policy] = {"id": run.experiment_id, "items": items}
                    stamp = run.run_name.split("-", 1)[1]
                    with self.job_lock:
                        self.job = {"state": "running", "completed": len(runs)}
            self.client.flush()
            with self.job_lock:
                self.last_result = self.pair(stamp, datetime.now(timezone.utc).isoformat(), runs)
                self.job = {"state": "done", "completed": len(runs)}
        except (Exception, SystemExit) as error:
            with self.job_lock:
                self.job = {"state": "error", "completed": self.job["completed"], "message": ui_message(error)}
