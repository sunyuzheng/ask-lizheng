# 调用问问立正

模型凭证留在服务端。网站与其他调用者使用同一问答接口，不直接调用 Builder Space，也不能从请求覆盖模型或系统指令。账号与每日额度默认关闭；启用后所有人每天3次，服务端核验的Founding Member每日不限次。端点受到输入、并发和频率限制，不提供持久对话 ID。

2026-10-04已核实的当前处理链：默认`deepseek-v4-flash`通过Builder Space转发至DeepSeek官方API；`text-embedding-3-small`通过同一网关转发至OpenAI。生成请求包含问题、处境、最近最多6轮问题与回答摘要及公开资料；向量请求只包含用于检索的问题、处境及必要时的上一轮问题，并受长度限制。Builder网关只保存用量元数据，不保存问答正文；本产品问答存档仍由Upstash承担。未发现本应用调用Tavily或网络搜索工具。更换模型或处理方时，须同步App同意披露、隐私政策和本机同意版本，不能据模型名推断未核实的服务保护承诺。

## 问一个问题

```sh
curl -N http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"跟着 AI 做出工具，换个需求就不会了，怎样练自己的判断？","intent":"apply","context":"希望能自己诊断问题，不打算手写每行代码。","history":[]}'
```

上线后把 localhost 换成正式站点地址。`intent` 可用 `understand`、`apply`、`find`；`context` 和 `history` 选填。问题最长 2000 字符，背景最长 2500 字符，历史最多六轮，每轮只有 question（2000 字符）和 summary（1500 字符）。整个请求最大 80 KB。历史由调用者提供，服务端将其视为未核实的上下文；新的明确主题会重新检索。

2026-10-04本地审核修复候选：App在单独披露和取得同意后，附上`ai_consent_model`（1–128字符），表示用户同意的回答模型。它不能用来选择模型。服务端在预留额度、存档或发送给AI前比对当前模型；不匹配返回HTTP409和`{"code":"ai_consent_changed","model":"当前模型"}`，客户端展示新模型并重新征求同意。请求一旦通过比对，本次生成和记录固定用捕获的模型；同意字段不会发给模型。旧浏览器请求不带该字段仍可调用。发布时须先部署支持该字段的后端，再同步个人站提供的App前端；第三方处理方与同等保护材料未确认前，不发布此候选。

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

上述事件只示意字段；实际 sources 附有日期、作者、归属等来源元数据，answered 至少有一个带来源编号的 section。初选材料可能在语义匹配后重新排序，编号以最后的 result 为准；provisional 的条目不能当成已经采用的引用。result 的出处按读者读到的先后编为1到n，正文角标随之改写，和保存图片、PDF的编号一致；partial 仍用候选材料的编号，好与已显示的候选对上。没有词法命中时可以省略 initial sources，语义失败则保留词法结果；缺少说话人身份的请求可能直接返回 clarify。checking 校验编号、输出格式和禁止生成的链接等，不是对事实正确性的保证。格式或引用失败后，repairing 状态表示正在进行唯一一次修复。读者只看得到角标数字，看不到 S 编号：编号只能以 `[S1]` 出现在 summary、正文和 limitations 里；把编号当名字写进句子（「S1 说得更直接」）或写进标题、追问、推荐理由的，也请模型改写这一次，改写后仍有或来不及改写的，服务器换成出处标题（能显示角标的地方另加角标）。只为措辞请求的改写（这条和点名立正那条）失败时，返回第一份回答，不当作失败。

v4 提示下 status 为 answered 的 result 另带 `share`（record_id、word、proof），提问者的页面用它在 lizheng.ai 生成分享页，见[记录协议](QUERY_RECORDS.md)「分享这条回答」。模型输出里的 `slug`（1到3个英文词）只用来组成分享地址，不出现在 result 中。

sections 含 heading、body、source_ids，以及 source / synthesis / application 的 kind；分别表示材料转述、AI 综合与联系处境的应用。sources 含 id、title、url、date、excerpt、author、attribution_note、evidence_role、推荐理由及可用的 timecode / public_copy_url。链接和原文片段由服务器提供。

partial 每次是当前完整段落的快照，调用者应替换前一个快照、按 ID 合并来源。服务器只显示已读到 answered 状态之后的完整段落，并逐段执行与最终回答相同的来源编号、目录条目、链接和引文检查；这不是对论断事实正确性的保证，也不代表整份回答已经通过校验。若模型不按提示先输出 status，可能没有提前段落，但最终回答仍会校验。repairing 时撤回暂存段落，result 时以最终回答与最终来源替换；停止、断流或超时时不把 partial 当成功，保留问题和可阅读的材料。不会转发或保存 provider 的原始 reasoning_content。

status 还有 clarify（缺少实质条件）、unsupported（资料不支持）和 sources-only（找到材料，但未连接模型、服务故障或仅有目录）。服务失败的 sources-only 仍可正常阅读原文。无法读取资料或处理请求时返回 error 事件。校验、频率与并发拒绝在 SSE 开始前返回 HTTP 422 / 413 / 429 / 503；429 带 Retry-After。调用者应处理停止、断线和未收到最终事件，不能把一条 progress 当成功。

语义匹配与模型等待期间，每 5 秒发送约 16KB 的标准 SSE 注释作为保活，并在整理回答阶段主动冲刷思路摘要，避免中间代理缓冲较小的数据包；注释不代表新的进度或思考。响应使用 no-store,no-transform。客户端应忽略注释，保留未完成问题与已有材料。模型故障的 sources-only 带 retryable:true 与固定 failure_code；用户明确重试后才重新发起请求。主页转发层在 100 秒期限或断流时返回 relay_timeout / upstream_stream_interrupted，客户端另设 110 秒期限并取消上游。重试在原回合进行，沿用内存中的原问题、背景、意图和历史，不自动追加或反复调用模型。

## 查看资料与状态

`GET /api/search?q=...` 提供无需生成回答的词法与判断卡检索，适合找原文。`GET /api/meta` 返回公开资料版本、收录数量、model_ready、semantic_ready 和 mode；`GET /health` 在资料未就绪时返回 503。

## 服务端怎样调用 Builder

在线时并行查找本地词法材料与调用 `/backend/v1/embeddings` 获取问题向量；本地找到的材料先发给读者，8 秒预算内语义请求失败便保留词法与判断卡结果。公开原文向量在发布前离线构建，在线请求不改写索引。证据包与必要前文送至 `/backend/v1/chat/completions`，默认模型为 `grok-4.5`（reasoning_effort: medium），使用严格 JSON schema；核心摘要最多 350 字符、解释段落最多三节。Grok 4.5 与候选 `deepseek-v4-flash` 使用真实 provider SSE，读取完整段落后发布 partial；网关忽略 stream 并返回完整 JSON 时仍按原校验流程处理，不为此多发一次模型请求。送给模型的每份材料保留完整 excerpt 和归属字段，链接及检索内部字段由服务器持有。

服务端选择 `gpt-5` 时附带 `reasoning_effort: low`；`deepseek-v4-pro` 和 `deepseek-v4-flash` 使用 JSON object 加同一 schema 提示，分别请求 thinking enabled / high 与 enabled / low，均保留服务端来源与格式校验。Builder 是否将这些参数转发并实际生效尚未得到可验证信息。Flash 的紧凑提示以 400–700 字与两段为目标，不把篇幅目标当硬性保证；仍执行通用长度、归属与条件要求。生成与至多一次结构修复共用 85 秒预算；不对网络或授权失败盲目重试。`/api/meta` 公开当前 model 与请求的 reasoning_effort，客户端不能覆盖。

输入及检索到的公开片段由Builder模型服务处理，默认模型仍为`deepseek-v4-flash`。旧`query_log_notice:"v1"`仅保存提问及元数据30天。新页面确认v3配置并展示提示后，提交`query_log_notice:"v3",conversation_id:<UUID>`，问题先可靠保存，完整已核验回答和当时所用来源在result发送前确认保存，持续至所有者手动删除。不关联账号/邮箱，不单独保存背景或历史摘要；回答可能引用背景，不保存模型内部推理。失去存档确认时明确返回`answer_archive_failed`且不扣额度。缺少提示版本的客户端不开始存档；第三方模型服务的处理规则由其管理。详见[记录协议](QUERY_RECORDS.md)。
