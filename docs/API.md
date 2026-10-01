# 调用问问立正

模型凭证留在服务端。网站与其他调用者使用同一公开问答接口，不直接调用 Builder Space，也不能从请求覆盖模型或系统指令。第一版没有账号，端点受到输入、并发和频率限制，不提供持久对话 ID。

## 问一个问题

```sh
curl -N http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"跟着 AI 做出工具，换个需求就不会了，怎样练自己的判断？","intent":"apply","context":"希望能自己诊断问题，不打算手写每行代码。","history":[]}'
```

上线后把 localhost 换成正式站点地址。`intent` 可用 `understand`、`apply`、`find`；`context` 和 `history` 选填。问题最长 2000 字符，背景最长 2500 字符，历史最多六轮，每轮只有 question（2000 字符）和 summary（1500 字符）。整个请求最大 80 KB。历史由调用者提供，服务端将其视为未核实的上下文；新的明确主题会重新检索。

返回 `text/event-stream`。progress 提供检索和理解状态；result 是一次完整且已经校验的 JSON。它不逐字流出未经核对的回答。

```text
event: progress
data: {"stage":"retrieving","message":"正在查找相关公开材料…"}

event: progress
data: {"stage":"thinking","message":"正在结合材料理解你的问题…"}

event: result
data: {"status":"answered","summary":"…","sections":[],"sources":[],"followups":[],"clarifying_questions":[],"limitations":"…"}
```

上面 result 仅示意字段；实际 answered 至少有一个带来源编号的 section。sections 含 heading、body、source_ids，以及 source / synthesis / application 的 kind；分别表示材料转述、AI 综合与联系处境的应用。sources 含 id、title、url、date、excerpt、author、attribution_note、evidence_role、推荐理由及可用的 timecode / public_copy_url。链接和原文片段由服务器提供。

status 还有 clarify（缺少实质条件）、unsupported（资料不支持）和 sources-only（找到材料，但未连接模型、服务故障或仅有目录）。服务失败的 sources-only 仍可正常阅读原文。无法读取资料或处理请求时返回 error 事件。校验、频率与并发拒绝在 SSE 开始前返回 HTTP 422 / 413 / 429 / 503；429 带 Retry-After。调用者应处理停止、断线和未收到最终事件，不能把一条 progress 当成功。

## 查看资料与状态

`GET /api/search?q=...` 提供无需生成回答的词法与判断卡检索，适合找原文。`GET /api/meta` 返回公开资料版本、收录数量、model_ready、semantic_ready 和 mode；`GET /health` 在资料未就绪时返回 503。

## 服务端怎样调用 Builder

在线时先向 `/backend/v1/embeddings` 获取问题向量，8 秒预算内失败便回到词法与判断卡。公开原文向量在发布前离线构建，在线请求不改写索引。证据包与必要前文送至 `/backend/v1/chat/completions`，使用 `gpt-5` 和严格 JSON schema。生成与至多一次结构修复共用 85 秒预算；不对网络或授权失败盲目重试。

输入及检索到的公开片段会由 Builder 的模型服务处理。本项目不记录问答正文，不创建对话数据库；它不能替第三方服务承诺保存政策。
