# Langfuse 工单知识库 Demo

同一个问题“怎样批量导出工单？”，旧手册让用户去工单列表点“批量下载”，现行手册要求从报表中心创建导出任务。这个 Demo 用真实模型回答问题，在本地 Langfuse 查看检索内容、模型输入输出和提示词版本，再用两道带参考答案的题验证修复。

## 运行

需要 Docker、支持 `!override` 的 Docker Compose（2.24.4 或更高）、Python 3 和 `uv`。首次启动会下载 Langfuse **v4.86.0** 的固定版本 Compose 文件、镜像和 Python 依赖。Langfuse Web、Worker、PostgreSQL、ClickHouse、Redis、MinIO 使用独立的 Compose 项目和数据卷。

```sh
./demo up
```

打开 [工单知识库工作台](http://127.0.0.1:8765/)，即可在网页中运行问答、查看引用、提交反馈和对比实验。首次启动会创建提示词和两道回归样本。已有的 Langfuse 容器正在运行时，执行 `./demo ui` 可以单独启动网页。网页仅监听本机地址，静态资源随项目提供。

需要查看完整追踪或编辑提示词时，运行 `./demo login` 获取本机账号，登录 [Langfuse 原生控制台](http://127.0.0.1:3100) 的“工单知识库”项目。重复启动会保留已有提示词、样本和追踪。

模型使用 Wylon 的 `https://api.wylon.cn/v1` 和 `ZhipuAI/GLM-5.3`。新环境可运行 `./demo set-key` 隐藏输入密钥，也可通过 `WYLON_API_KEY` 传入。密钥仅由本机 Python 进程读取。模型请求会将提问与选中的演示手册片段发给 Wylon，追踪和评分写入本地 Langfuse。

## 从网页体验

| 页面 | 可以操作什么 |
| --- | --- |
| [问答工作台](http://127.0.0.1:8765/#workbench) | 选择“含归档”，运行“怎样批量导出工单？”，再用“仅现行”运行同一题。点击回答中的文档 ID 查原文，或点击 Trace 查看调用明细。“最近问答”可来回对照同一会话的记录。 |
| [回归实验](http://127.0.0.1:8765/#experiments) | 查看最近一组策略对照，展开每题核对两份回答及检索、引用、步骤三项评分。“运行回归”会用当前模型、提示词和数据集重新比较。 |
| [手册索引](http://127.0.0.1:8765/#manuals) | 按标题、关键词或 ID 搜索，筛选现行和归档文档。“用此主题提问”将示例问题带回工作台。 |

问答页面的“有帮助 / 无帮助”会向该 Trace 写入一条可更新的 `user-helpful` 评分。“关联回归样本”会把这条 Trace 记作对应参考样本的来源。网页在等待 Langfuse 收录追踪时先显示模型回答，随后补齐耗时及 Token 用量。浏览历史、阅读手册、打开已完成的实验均只读取本地数据。

## 复现错误并检查修复

1. 用旧策略回答预设的导出问题，并将对应测试题的来源关联到真实请求。

   ```sh
   ./demo ask --strategy baseline --save-case "怎样批量导出工单？"
   ```

   打开输出的 Trace 链接。展开 `lookup-manual` 查看命中的手册，展开 `glm-answer` 查看送给模型的消息、实际回答、提示词版本及供应商返回的 token 用量。此例的检索器对关键词计分，命中数相同时优先索引中靠前的文档，因此旧策略命中已归档的 `EXPORT-2024`。

2. 运行 `./demo feedback <Trace_ID> down`，用上一步输出的 32 位 Trace ID 替换占位符。这会写入一条名为 `user-helpful` 的布尔评分，模拟用户点击“无帮助”。

3. 切换检索策略。新策略在排名前过滤归档手册，模型、提示词及知识库内容保持一致。

   ```sh
   ./demo ask --strategy current "怎样批量导出工单？"
   ```

   新追踪应命中 `EXPORT-2026`，可对照两条 Trace 判断不同答案从哪一步开始分化。两条命令默认共享一个 Session，使用 `--session` 可以更换会话。

## 比较两次实验

```sh
./demo compare
```

命令从本地 Langfuse 读取 `workorder-regression` 数据集和标记为 `production` 的提示词，在同一批样本上分别运行 `baseline`、`current`。初始数据集有两道题，完整比较会产生四次供应商模型调用。终端给出每题评分，点末尾的“并排比较”链接即可在 Langfuse 的 Experiments 页面按题对照两个版本的输出。

| 评分 | 判定方法 |
| --- | --- |
| `retrieval-correct` | 实际检索到的手册 ID 与参考 ID 一致。 |
| `citation-correct` | 从模型答案里解析出方括号引用，引用集合仅包含预期的现行文档 ID。 |
| `steps-correct` | 回答包含参考菜单词，且没有使用已弃用的操作词。 |

导出题用来发现旧资料，时区题用来检查修复是否影响原本正确的回答。三个指标测的是预设文档和菜单，模型的整体回答质量仍需人工阅读。费用依赖上报值或 Langfuse 的模型价格匹配，本示例只上报供应商实际返回的用量。

## 修改和停止

编辑 `knowledge.json` 可修改演示手册。在 Langfuse 的 Prompts 页面可以为 `workorders/answer` 创建新版本并移动 `production` 标签，新启动的问答会取到该版本。筛选策略和评分函数在 `workorder.py` 中，样本附有独立参考答案。

```sh
./demo status
./demo down
```

`down` 停止本 Demo 的网页进程和容器，并保留数据卷。之后用 `./demo up` 可继续查看记录。本机配置和网页进程日志保存在受 Git 忽略的 `.local/` 目录，里面的 `stack.env` 与数据卷配套使用。
