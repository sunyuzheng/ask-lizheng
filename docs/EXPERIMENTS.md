# 开发验证与付费实验

负责人要决定什么时候证据已经够用。投入应换来有用的判断；调用量、测试轮数和分数表都不是进度。

## 先确定要交付什么

开始修改时，把用户能看到的效果、阻止发布的问题和完成条件说清楚。验证范围由改变的行为决定：机械转换先用单元测试，资料覆盖先查冻结原文与检索，回答质量有未决问题时才调用真实模型。顺手发现的其他改进放进后续工作，不能不断抬高本轮的完成条件。

评审器先用少量已知正确、已知错误的记录校准，确认它能区分待解决的问题再扩大。版本号、代码哈希、评测脚本或统计口径变化，都不自动要求重新生成答案。已有输出能回答的问题，离线重算。多次采样用于具体的波动问题，不默认全题集三轮；增加并发只改变速度，不增加证据。

如果另一个结果也不会改变采用、拒绝或下一步行动，就停止实验并交付。留出的风险如实记录；不用增加测试来消除一切不确定性。

## 付费入口

`answer_benchmark.py generate` 和 `judge` 默认只显示题目、轮数和工作量，**不联网、不读凭证、不覆盖报告**，生成默认一轮。`evidence_eval.py --refresh-vectors`、`retrieval_eval.py --refresh-vectors` 同样先预览；普通离线检索和证据检查照常运行。旧 `answer_eval.py`、`server.live_eval` 的付费入口已停用，历史结果保留。

```sh
python scripts/answer_benchmark.py generate --cases CASE_ID --output .local/check-a.json
python scripts/answer_benchmark.py generate --cases CASE_ID --output .local/check-a.json \
  --execute --experiment-plan .local/experiment.json
```

由执行者在 `.local/experiment.json` 写本次计划，不需要把设计工作交还用户。下面只是字段示例，题目、模型和金额是占位值，**不是价格或推荐预算**；根据待决策问题、实际输入大小、输出上限和既有成本证据估算。

```json
{
  "id": "specific-decision-20261101",
  "decision": "本次采用或拒绝哪项改动",
  "uncertainty": "现有证据还不能确定什么",
  "decision_signal": "什么结果会改变采用、拒绝或下一步行动",
  "stop_condition": "哪些结果一出现就停止，未解决时怎样收尾",
  "case_ids": ["CASE_ID"],
  "max_cases": 1,
  "max_repeats": 1,
  "max_requests": 4,
  "max_request_bytes": 50000,
  "max_output_tokens": 4000,
  "budget_usd": 2,
  "estimated_total_usd": 1,
  "reservation_usd_per_request": {"MODEL_NAME": 0.5}
}
```

`reservation_usd_per_request` 同时是模型白名单；要计入选材、生成、修复、备用模型、向量和评审的请求。用同一计划覆盖这些阶段。不列具体题目、要跑整组时需要 `why_full_scope`；超过一轮需要 `why_repeats`；`--fresh` 或 `--retry-errors` 需要 `new_signal`，说明相对已有结果多知道什么。需要允许中断恢复时，初始计划就说明补齐失败记录的决策价值与范围。先选最小能区分结果的范围，不能把字段填完当成实验值得做的证明。

执行时每次请求都先检查请求数、输入字节数、显式输出上限、模型和累计费用预留，并用同一凭证读取账户花费。并发和流式调用、修复、备用、后续评审共用带文件锁的账本。超界、认证/计费/限流错误、费用不可读都会停止后续调用；已发出的并发请求仍计入预算，不宣称可以撤销。不截断输入来假装通过，不自动追加预算。

费用预留是估计，平台账单可能延迟，账户内其他流量也会进入花费变化，**不能保证实际美元账单绝不超出估计**。确定限制的是发出的请求数、输入大小和请求里的输出上限；账户余额不作为任务预算。账户读数不代表精确的本任务归因。

## 复用、恢复与收尾

报告逐题保存，向量逐批保存。旧报告不覆盖，恢复时写新文件并指向旧报告：

```sh
python scripts/answer_benchmark.py generate --cases CASE_ID --output .local/check-reused.json \
  --reuse-only --reuse-report .local/check-a.json
```

`--reuse-only` 不需要凭证，并使用禁止联网的传输层；缺少可复用结果会停止。生成复用核对实现、模型、真实输入和来源绑定，并重新校验答案；评审复用核对完整请求（含评审说明、标准答案和盲序），不把旧时延计成新测量。不同实现的旧报告不能直接冒充新版本运行；先核对实际行为差异，能离线验证的离线完成。输入或评审变化不会静默触发付费重跑。

中断后确需补齐时，沿用同一计划和剩余预算，使用 `--execute --reuse-report OLD --retry-errors`，只重试失败部分，已成功的答案与已完成的选材保留。强制重做需要 `--fresh` 和明确的新信号。预算已经耗尽时，不能换目录、重置 id 或写临时 API 循环绕开停止。确有新的决策问题才另立实验，记录此前结论、增量投入与停止条件。

账本在 `~/.local/state/ask-lizheng/experiments/<id>/`，跨进程、阶段和工作树共用；每份计划按内容哈希保存，同 id 改计划不会重置额度。私有报告留在项目 `.local/`，费用与计划留在私有目录；不公开密钥、账户账单或原始推理。账本只保存请求哈希与用量边界，不保存提示词或凭证。

```sh
python scripts/experiment_guard.py status --experiment-plan .local/experiment.json
python scripts/experiment_guard.py conclude --experiment-plan .local/experiment.json \
  --outcome adopt --evidence '得到的信号、决定、剩余限制；为什么无需再跑'
```

结论为 `adopt`、`reject` 或 `inconclusive`。收尾后账本拒绝新请求。实验机制约束本仓库的开发评测入口；正常访客问答和已批准的资料索引维护沿用各自的运行机制。全局开发原则由 `~/.codex/AGENTS.md` 管理，其他项目采用各自适合的验证入口，不复制本项目的服务细节。

## 本次流程修复的验证

只用离线与模拟接口验证：默认零调用、有凭证也不会自动执行；范围和重跑需要理由；多阶段与并发共享上限；费用不可读或边界失败会停；换工作树或改计划不能重置额度；成功答案、选材和评审可复用，输入变化不会静默重跑；结果与账本不泄露凭证。没有调用真实模型来验证这个付费保护机制。

本轮相关检查共68项通过：`python -m pytest tests/test_experiment_guard.py tests/test_answer_benchmark.py tests/test_evidence_benchmark.py tests/test_retrieval_eval.py tests/test_answer_contract.py -q`。两个退役入口的 CLI 冒烟均报告零网络请求。自审确认改动限于开发入口、指导和验证记录；生产检索、选材和回答逻辑未改，因此不重跑付费质量实验，也不为开发工具重新部署问答服务。
