"""Trace a small versioned knowledge base and compare two retrieval policies in Langfuse."""

import argparse
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from langfuse import Evaluation, Langfuse, propagate_attributes
from langfuse.api.core.api_error import ApiError

from local_setup import BASE_URL, MODEL_KEY, settings


KB = json.loads(Path(__file__).with_name("knowledge.json").read_text())
PROMPT = "workorders/answer"
DATASET = "workorder-regression"
MODEL = "ZhipuAI/GLM-5.3"
PROVIDER = "https://api.wylon.cn/v1/"
POLICIES = ("baseline", "current")
SCORE_NAMES = ("retrieval-correct", "citation-correct", "steps-correct")
INSTRUCTIONS = """你是工单产品客服。根据以下手册片段回答用户，保留界面名称，用一到两句话。
只使用片段中的事实。结尾原样标注来源，格式为 [文档ID]。资料不足时说明无法确认。

手册片段
{{context}}"""


@contextmanager
def local_langfuse(config):
    credentials = (config["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"], config["LANGFUSE_INIT_PROJECT_SECRET_KEY"])
    with httpx.Client(base_url=BASE_URL + "/api/public/", auth=credentials,
                      timeout=30, trust_env=False) as api:
        client = Langfuse(public_key=credentials[0], secret_key=credentials[1], base_url=BASE_URL,
                          environment="local", timeout=30, httpx_client=api)
        try:
            yield client, api
        finally:
            client.shutdown()


def record_feedback(client, trace_id, vote):
    client.create_score(trace_id=trace_id, name="user-helpful", value=int(vote == "up"),
                        data_type="BOOLEAN",
                        score_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "local-vote/" + trace_id)))


def retrieve(question, policy):
    # Equal keyword scores keep fixture order, reproducing an archived document winning a tie.
    candidates = [doc for doc in KB["documents"] if policy == "baseline" or doc["status"] == "current"]
    ranked = [(sum(term in question for term in doc["keywords"]), doc) for doc in candidates]
    score, best = max(ranked, key=lambda row: row[0], default=(0, None))
    return best if score else None


def model_client():
    key = os.environ.get("WYLON_API_KEY") or (MODEL_KEY.read_text().strip() if MODEL_KEY.exists() else "")
    if not key:
        raise SystemExit("请运行 ./demo set-key 或设置 WYLON_API_KEY。")
    return httpx.Client(base_url=PROVIDER, headers={"Authorization": f"Bearer {key}"},
                        timeout=httpx.Timeout(120, connect=12), follow_redirects=False)


def generate(provider, messages):
    response = provider.post("chat/completions", json={
        "model": MODEL, "messages": messages, "temperature": 0, "max_tokens": 512,
    })
    if not response.is_success:
        raise RuntimeError(f"Wylon 模型调用返回 HTTP {response.status_code}。")
    payload = response.json()
    choice = (payload.get("choices") or [{}])[0]
    answer = (choice.get("message", {}).get("content") or "").strip()
    if not answer:
        raise RuntimeError(f"模型没有返回答案正文，结束原因 {choice.get('finish_reason')}。")
    counts = payload.get("usage") or {}
    usage = {key: counts[field] for key, field in
             (("input", "prompt_tokens"), ("output", "completion_tokens"), ("total", "total_tokens"))
             if isinstance(counts.get(field), int)}
    return answer, usage


def answer_question(client, provider, prompt, question, policy):
    with client.start_as_current_observation(name="workorder-answer", as_type="chain",
                                             input=question, metadata={"retrieval_policy": policy}) as root:
        with client.start_as_current_observation(name="lookup-manual", as_type="retriever",
                                                 input=question, metadata={"policy": policy}) as lookup:
            document = retrieve(question, policy)
            lookup.update(output=document)
        if document:
            context = f"文档ID {document['id']}\n{document['text']}"
            messages = [{"role": "system", "content": prompt.compile(context=context)},
                        {"role": "user", "content": question}]
            with client.start_as_current_observation(name="glm-answer", as_type="generation", model=MODEL,
                                                     input=messages, prompt=prompt,
                                                     model_parameters={"temperature": 0, "max_tokens": 512}) as generation:
                answer, usage = generate(provider, messages)
                generation.update(output=answer, usage_details=usage)
        else:
            answer = "当前手册没有相关资料，无法确认。"
        output = {"answer": answer, "retrieved_id": document["id"] if document else None}
        root.update(output=output)
        return output, root.trace_id, root.id


def save_case(client, case, trace_id=None, observation_id=None):
    expected = {key: case[key] for key in ("source", "required_terms", "obsolete_terms")}
    return client.create_dataset_item(
        dataset_name=DATASET, id=case_id(case), input={"question": case["question"]},
        expected_output=expected, source_trace_id=trace_id, source_observation_id=observation_id,
    )


def case_id(case):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, DATASET + "/" + case["question"]))


def prepare(client):
    try:
        client.api.prompts.get(PROMPT, label="production")
    except ApiError as exc:
        if exc.status_code != 404:
            raise
        client.create_prompt(name=PROMPT, type="text", prompt=INSTRUCTIONS, labels=["production"],
                             commit_message="工单手册问答初始模板")
    try:
        dataset = client.get_dataset(DATASET)
        existing = {item.id for item in dataset.items}
    except ApiError as exc:
        if exc.status_code != 404:
            raise
        client.create_dataset(name=DATASET, description="导出菜单回归及不受改动影响的时区对照题")
        existing = set()
    for case in KB["cases"]:
        if case_id(case) not in existing:
            save_case(client, case)


def evaluate(*, output, expected_output, **kwargs):
    text = output["answer"]
    source = expected_output["source"]
    citations = re.findall(r"\[([A-Z][A-Z0-9-]*)\]", text)
    correct_steps = (all(term in text for term in expected_output["required_terms"])
                     and not any(term in text for term in expected_output["obsolete_terms"]))
    return [
        Evaluation(name="retrieval-correct", value=int(output["retrieved_id"] == source)),
        Evaluation(name="citation-correct", value=int(set(citations) == {source})),
        Evaluation(name="steps-correct", value=int(correct_steps)),
    ]


def experiment_runs(client, provider, prompt):
    dataset = client.get_dataset(DATASET)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    for policy in POLICIES:
        def task(*, item, **kwargs):
            result, _, _ = answer_question(client, provider, prompt, item.input["question"], policy)
            return result

        run = dataset.run_experiment(
            name=DATASET, run_name=f"{policy}-{stamp}", task=task, evaluators=[evaluate], max_concurrency=1,
            metadata={"policy": policy, "prompt_version": str(prompt.version), "model": MODEL},
        )
        if len(run.item_results) != len(dataset.items) or not run.item_results:
            raise RuntimeError("实验样本没有全部执行，请检查模型响应与 Langfuse。")
        for row in run.item_results:
            if {score.name for score in row.evaluations} != set(SCORE_NAMES):
                raise RuntimeError(f"该样本缺少评估结果，请在 Langfuse 查看 Trace {row.trace_id}。")
        yield policy, run


def comparison_url(project_id, run_ids):
    query = urlencode({"baseline": run_ids["baseline"], "c": run_ids["current"]})
    return f"{BASE_URL}/project/{project_id}/experiments/results?{query}"


def compare(client, provider, prompt):
    run_ids = {}
    for policy, run in experiment_runs(client, provider, prompt):
        run_ids[policy] = run.experiment_id
        print(f"\n{run.run_name}  {run.experiment_url}", flush=True)
        for row in run.item_results:
            scores = "  ".join(f"{score.name}={score.value}" for score in row.evaluations)
            print(row.item.input["question"], scores)
    print("并排比较", comparison_url(settings()["LANGFUSE_INIT_PROJECT_ID"], run_ids))


def wait_until_ready(config):
    auth = (config["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"], config["LANGFUSE_INIT_PROJECT_SECRET_KEY"])
    deadline = time.monotonic() + 240
    with httpx.Client(timeout=4, trust_env=False, auth=auth) as local:
        while time.monotonic() < deadline:
            try:
                if local.get(BASE_URL + "/api/public/projects").is_success:
                    return
            except httpx.RequestError:
                pass
            time.sleep(2)
    raise SystemExit("本地项目尚未就绪，运行 ./demo status 查看容器状态后可重试 ./demo up。")


def main():
    parser = argparse.ArgumentParser(description="工单知识库与 Langfuse 实验")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    ask = commands.add_parser("ask")
    ask.add_argument("question", nargs="?", default=KB["cases"][0]["question"])
    ask.add_argument("--strategy", choices=POLICIES, default="current")
    ask.add_argument("--session", default="workorder-local")
    ask.add_argument("--save-case", action="store_true", help="将该示例题的来源关联到本次 Trace")
    feedback = commands.add_parser("feedback")
    feedback.add_argument("trace_id", type=str.lower)
    feedback.add_argument("vote", choices=("up", "down"))
    commands.add_parser("compare")
    args = parser.parse_args()
    config = settings()
    if args.command == "init":
        wait_until_ready(config)
    with local_langfuse(config) as (client, _):
        if args.command == "init":
            prepare(client)
            print("Langfuse 和工单样本已就绪。", BASE_URL, "查看登录信息请运行 ./demo login。")
        elif args.command == "feedback":
            if not re.fullmatch(r"[0-9a-f]{32}", args.trace_id):
                parser.error("Trace ID 应为 32 位十六进制数。")
            record_feedback(client, args.trace_id, args.vote)
            print("用户反馈", args.vote, client.get_trace_url(trace_id=args.trace_id))
        else:
            prompt = client.get_prompt(PROMPT, label="production")
            with model_client() as provider:
                if args.command == "compare":
                    compare(client, provider, prompt)
                else:
                    case = next((c for c in KB["cases"] if c["question"] == args.question), None)
                    if args.save_case and case is None:
                        parser.error("--save-case 需要使用 knowledge.json 中附有标准答案的示例题。")
                    with propagate_attributes(session_id=args.session, tags=[args.strategy]):
                        output, trace_id, observation_id = answer_question(
                            client, provider, prompt, args.question, args.strategy)
                    if args.save_case:
                        save_case(client, case, trace_id, observation_id)
                    print(output["answer"])
                    print(f"检索文档 {output['retrieved_id']} · 提示词 v{prompt.version}")
                    print("Trace ID", trace_id)
                    print(client.get_trace_url(trace_id=trace_id))


if __name__ == "__main__":
    main()
