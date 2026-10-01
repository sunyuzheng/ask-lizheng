# 调用问问立正

模型凭证留在服务端。网站与其他调用者使用同一问答接口，不直接调用 Builder Space，也不能从请求覆盖模型或系统指令。账号与每日额度默认关闭；启用后所有人每天3次，服务端核验的Founding Member每日不限次。端点受到输入、并发和频率限制，不提供持久对话 ID。

## 问一个问题

```sh
curl -N http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"跟着 AI 做出工具，换个需求就不会了，怎样练自己的判断？","intent":"apply","context":"希望能自己诊断问题，不打算手写每行代码。","history":[]}'
```

上线后把 localhost 换成正式站点地址。`intent` 可用 `understand`、`apply`、`find`；`context` 和 `history` 选填。问题最长 2000 字符，背景最长 2500 字符，历史最多六轮，每轮只有 question（2000 字符）和 summary（1500 字符）。整个请求最大 80 KB。历史由调用者提供，服务端将其视为未核实的上下文；新的明确主题会重新检索。

返回 `text/event-stream`。progress 提供服务器实际到达的处理阶段；sources 在模型回答之前提供可阅读的候选材料；approach 提前提供结合检索材料的整理方向、待核对的问题与出处；partial 提供已完整返回且通过来源编号与格式检查的段落；result 是一次完整且已经校验的 JSON。approach 是公开的回答准备摘要，尚不是结论；不是模型内部推理文本。完整回答仍须经过格式与来源编号核对。

```text
event: progress
data: {"stage":"retrieving","message":"正在查找相关公开材料…"}

event: sources
data: {"phase":"initial","provisional":true,"sources":[{"id":"S1","title":"…","excerpt":"…","url":"…"}]}

event: progress
data: {"stage":"matching","message":"正在匹配意思相近的材料，并合并重复出处…"}

event: sources
data: {"phase":"matched","provisional":true,"sources":[{"id":"S1","title":"…","excerpt":"…","url":"…"}]}

event: approach
data: {"summary":"先对照原文，解释关键关系并核对条件。","questions":["这份产出会改变下游的什么决定？"],"sources":[{"id":"S1","title":"…"}],"note":"这是整理方向，尚不是完整回答。"}

event: progress
data: {"stage":"thinking","message":"已找到 12 个候选片段，正在根据材料整理回答…"}

event: progress
data: {"stage":"drafting","message":"回答内容已开始返回，正在整理完整段落与出处…"}

event: partial
data: {"sections":[{"heading":"…","body":"…","source_ids":["S1"],"kind":"synthesis"}],"sources":[{"id":"S1","title":"…","url":"…","excerpt":"…"}]}

event: progress
data: {"stage":"checking","message":"回答已生成，正在核对来源编号和输出格式…"}

event: result
data: {"status":"answered","summary":"…","sections":[],"sources":[],"followups":[],"clarifying_questions":[],"limitations":"…"}
```

上述事件只示意字段；实际 sources 附有日期、作者、归属等来源元数据，answered 至少有一个带来源编号的 section。初选材料可能在语义匹配后重新排序，编号以最后的 result 为准；provisional 的条目不能当成已经采用的引用。没有词法命中时可以省略 initial sources，语义失败则保留词法结果；缺少说话人身份的请求可能直接返回 clarify。checking 校验编号、输出格式和禁止生成的链接等，不是对事实正确性的保证。格式或引用失败后，repairing 状态表示正在进行唯一一次修复。

sections 含 heading、body、source_ids，以及 source / synthesis / application 的 kind；分别表示材料转述、AI 综合与联系处境的应用。sources 含 id、title、url、date、excerpt、author、attribution_note、evidence_role、推荐理由及可用的 timecode / public_copy_url。链接和原文片段由服务器提供。

partial 每次是当前完整段落的快照，调用者应替换前一个快照、按 ID 合并来源。服务器只显示已读到 answered 状态之后的完整段落，并逐段执行与最终回答相同的来源编号、目录条目、链接和引文检查；这不是对论断事实正确性的保证，也不代表整份回答已经通过校验。若模型不按提示先输出 status，可能没有提前段落，但最终回答仍会校验。repairing 时撤回暂存段落，result 时以最终回答与最终来源替换；停止、断流或超时时不把 partial 当成功，保留问题和可阅读的材料。不会转发或保存 provider 的原始 reasoning_content。

status 还有 clarify（缺少实质条件）、unsupported（资料不支持）和 sources-only（找到材料，但未连接模型、服务故障或仅有目录）。服务失败的 sources-only 仍可正常阅读原文。无法读取资料或处理请求时返回 error 事件。校验、频率与并发拒绝在 SSE 开始前返回 HTTP 422 / 413 / 429 / 503；429 带 Retry-After。调用者应处理停止、断线和未收到最终事件，不能把一条 progress 当成功。

语义匹配与模型等待期间，每 5 秒发送约 16KB 的标准 SSE 注释作为保活，并在整理回答阶段主动冲刷思路摘要，避免中间代理缓冲较小的数据包；注释不代表新的进度或思考。响应使用 no-store,no-transform。客户端应忽略注释，保留未完成问题与已有材料。模型故障的 sources-only 带 retryable:true 与固定 failure_code；用户明确重试后才重新发起请求。主页转发层在 100 秒期限或断流时返回 relay_timeout / upstream_stream_interrupted，客户端另设 110 秒期限并取消上游。重试在原回合进行，沿用内存中的原问题、背景、意图和历史，不自动追加或反复调用模型。

## 查看资料与状态

`GET /api/search?q=...` 提供无需生成回答的词法与判断卡检索，适合找原文。`GET /api/meta` 返回公开资料版本、收录数量、model_ready、semantic_ready 和 mode；`GET /health` 在资料未就绪时返回 503。

## 服务端怎样调用 Builder

在线时并行查找本地词法材料与调用 `/backend/v1/embeddings` 获取问题向量；本地找到的材料先发给读者，8 秒预算内语义请求失败便保留词法与判断卡结果。公开原文向量在发布前离线构建，在线请求不改写索引。证据包与必要前文送至 `/backend/v1/chat/completions`，默认模型为 `grok-4.5`（reasoning_effort: medium），使用严格 JSON schema；核心摘要最多 350 字符、解释段落最多三节。Grok 4.5 与候选 `deepseek-v4-flash` 使用真实 provider SSE，读取完整段落后发布 partial；网关忽略 stream 并返回完整 JSON 时仍按原校验流程处理，不为此多发一次模型请求。送给模型的每份材料保留完整 excerpt 和归属字段，链接及检索内部字段由服务器持有。

服务端选择 `gpt-5` 时附带 `reasoning_effort: low`；`deepseek-v4-pro` 和 `deepseek-v4-flash` 使用 JSON object 加同一 schema 提示，分别请求 thinking enabled / high 与 enabled / low，均保留服务端来源与格式校验。Builder 是否将这些参数转发并实际生效尚未得到可验证信息。Flash 的紧凑提示以 400–700 字与两段为目标，不把篇幅目标当硬性保证；仍执行通用长度、归属与条件要求。生成与至多一次结构修复共用 85 秒预算；不对网络或授权失败盲目重试。`/api/meta` 公开当前 model 与请求的 reasoning_effort，客户端不能覆盖。

输入及检索到的公开片段会由 Builder 的模型服务处理。本项目不记录问答正文，不创建对话数据库；它不能替第三方服务承诺保存政策。

## 账号与额度（默认关闭）

浏览器通过同源`GET /api/ask-lizheng/auth/session`读取enabled、authenticated、founding、remaining及reset_at；邮箱核验入口使用`/api/ask-lizheng/auth/login`，验证码发送与确认分别为同源POST `email-request`（email）及`email-verify`（code）；仅成功核验邮箱后查询Founding标签。原生表单与JSON调用均受同源校验，验证码10分钟、最多5次、一次消费。可选Academy SSO可显式选择provider=logto。退出为同源POST `/api/ask-lizheng/auth/logout`。前端携带同源cookie，不传会员档位。主页与独立页的请求经过同一Vercel转发；启用后Builder直接调用没有有效证明会被拒绝。

只有最终校验通过的answered回答结算一次；内部修复不重复计次，其他最终状态、失败及生成中的取消释放预留。SSE可包含quota事件，最终result可包含quota。HTTP429的quota_exhausted与一般rate_limited分开，前者有remaining及reset_at字段；转发只接收带专用X-Ask-Error-Code标记且有界的额度错误。重复attempt返回409，不缓存或重放回答。

内部`X-Ask-Admission`使用`v1.<base64url JSON>.<base64url HMAC-SHA256>`，无padding。签名覆盖ASCII `v1.`加encoded JSON，secret是至少32字节UTF-8。JSON精确字段为v:1、sub、tier(public/founding)、attempt(小写规范UUID)、exp(Unix整数秒)、method、path及body_sha256。POST固定path为/api/ask，摘要对应精确转发body；GET /api/quota的摘要对应空body。有效期由转发签发60秒，Builder上限120秒，不接受浏览器自行签发。证明、摘要及问题正文不进入额度账本。

详细规则、隐私边界及启用配置见[ACCOUNT_QUOTAS.md](ACCOUNT_QUOTAS.md)和[ACCOUNT_AUTH_HANDOFF.md](ACCOUNT_AUTH_HANDOFF.md)。
