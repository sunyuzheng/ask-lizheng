# 提问记录

## v4公开展示与处境存档（2026-10-02已定，前后端已实现）

立正看过后台后决定：真实的问题和回答比我们编的示例更值得读，以后在页面上展示去掉个人信息的问答（例如「今天大家在问什么」），补充或替代现在的示例问题；常见问题也是写文章、做视频的选题。说明要开诚布公：先讲为什么保存，再讲保存什么、怎么保护隐私，不让人去猜我们拿数据做什么。

- 输入框下方短句（位置不变，不放在页面开头）：「很多问题是共性的。提交即同意保存问答，去掉个人信息后可能整理公开，帮到更多人。请勿填写私密信息。」
- 「说明」细则分四段：为什么保存（很多问题是共性的；整理常见问答、去掉个人信息后公开，如「今天大家在问什么」；立正从中找选题写文章、做视频，并改进回答）；保存什么（提问、完整回答和所用出处，不关联邮箱、账号或IP）；你的隐私（公开前先用模型自动去掉可能认出人的信息，模型也可能漏，所以别填私密信息；「结合我的处境」只用于分析、不公开，用到它的回答也不公开）；另外（AI整理、非本人回复；发给Builder Space处理；刷新清空）。处境输入处另有一句「这部分只用于分析，不会公开。」
- 只有在v4提示下提交的问答可以公开；v3（提示写明「记录仅立正可查看」）和v1记录永不公开。存档须逐条保存提示版本。
- 「结合我的处境」原文随问答存档，只供所有者分析，永不公开；带处境的问答（intent为apply或带背景）一律不公开。
- Ops后台拆到独立仓库（另一会话负责），两边按下面的约定各自上线。
- 展示（2026-10-02）：主页和本页面首页的「别人在问什么」读Ops的公开发现接口，有已发布问答时替代示例问题。
- 写入（2026-10-02实现）：`/api/meta`宣布v4；v4请求的开始记录多带`notice_version: "v4"`、`has_background`（填了处境或有之前的对话轮次即为"1"）和处境原文（只给所有者，永不公开）。v3请求的记录形状不变。个人站writer接受两种形状并写入记录HASH。
- 自动发布（2026-10-02用户决定）：Ops每15分钟一轮（10-03用户改回；10-02曾改成每天一次）把新的、可公开的v4提问（无处境、首轮、非apply、已回答）发到本服务`/api/curate`，模型去掉个人信息、判断是否值得展示、归到主题；通过的直接发布，立正在Ops后台可撤下。签名密钥由Builder令牌按用途`ask-lizheng:curate:v1`派生，Ops只持有派生值。v1/v3记录永不公开，即使去掉个人信息。
- 主题首批（2026-10-02用户同意）：之前的提问不公开，但可以用共性主题起步。Ops把首轮、非apply的问题文本发到`/api/themes`一次，模型只返回不超过30个、各被问到至少两次的主题，每个用自己的话写成通用问题（服务端丢掉与原问题有12字以上相同片段的），`/api/seed-answer`照常回答后作为主题种子发布，之后的v4提问可以并入这些主题。

前后端约定：
- 前端（个人站主页与本仓库页面）已支持v4，但只在`/api/meta`宣布`ops_logging: {enabled: true, retention: "until_deleted", answer_archive: true, notice: "v4", context_archive: true, public_display: "deidentified"}`时显示v4提示，并在请求里带`query_log_notice: "v4"`、`conversation_id`和照常的`context`。字段不全或notice是未知版本时：主页退回v1提示与v1标记，本页面暂停发送并提示保存设置尚未确认。
- 个人站转发层对v4与v3一样签入匿名visitor和entrypoint。
- 后端（Builder与Ops存储）须先能接受`v4`（Builder请求模型目前只收v1/v3）、逐条保存提示版本和处境原文，再让meta宣布v4；宣布之前页面一直显示v3。

## v3私人问答归档候选

2026-10-02页面文案：输入框下面写匿名与用途——「提问是匿名的。问答会保存下来，用来改进回答；请勿填写私密信息。」；「说明」与v4一样分四段（为什么保存、保存什么、你的隐私、另外），写明记录只有立正能看到；「你的隐私」讲提问匿名（不关联邮箱、账号或IP，我们不知道是谁问的）、登录只核验Founding身份且不和提问记在一起，以及回答可能提到「结合我的处境」里填的内容。不写「不追踪」：页面访问和点击有匿名计数，记录里有匿名使用标识；登录状态加密保存12小时，所以不写「验证后立刻忘掉」。「站点所有者手动删除」、旧v1的30天、登录窗口草稿这类实现细节不写给用户；保存与删除的行为不变。

用户要求提问和回答一起存档，持续保存到所有者手动删除。旧v1提问记录继续原30天期限，不迁移、不延长。启用`ASK_OPS_ENABLED=true`需quota和query logging均ready，部署review用`--enable-quota --enable-query-log --enable-ops`；无需新增secret。

新页面先从meta确认`ops_logging.enabled=true,notice="v3",answer_archive=true,retention="until_deleted"`，显示问答保存提示后提交`query_log_notice:"v3"`及canonical UUID `conversation_id`。同一页面连续提问、重试沿用会话UUID，新对话重置。relay仅为v3签入匿名`visitor`和`entrypoint`；服务端再次HMAC为存档标识，不保存邮箱、账号、IP或独立的人数统计。

固定端点仍为个人站query-storage。证明为`X-Ask-Query-Proof: v3.<expiry>.<hex>`，45秒有效；HMAC域`ask-ops-store:v3:<expiry>:<sha256(compact UTF-8 body)>`。正文上限262144字节，Content-Type为application/octet-stream。start正文为`{v:3,event:'start',record_id,question,created_at,model,visitor_id,conversation_id,intent,entrypoint}`；finish正文为`{v:3,event:'finish',record_id,status,duration_ms,answer,error_code}`。ACK必须严格为`{"ok":true}`。

start在准入/额度预留后、模型调用前确认。final answer从服务端已核验的最终result捕获，字段仅status、summary、sections、sources、followups、clarifying_questions、limitations和可选retryable/failure_code；来源保留当时的标题、链接、作者、日期、片段、归属说明与时间点。排除quota、账号、原始provider JSON、partial和内部推理。不单独保存提交的背景/历史；回答可能引用背景，提交前明确提示。

每次写入最多2秒、最多两次，重试沿用UUID与冻结正文。start失败不调用模型并退额度。完整结果须等待finish ACK再发SSE result，生成槽先释放。final ACK无法确认则返回`answer_archive_failed`，不冒充成功、不扣这次额度，cleanup有界重试同一实际回答，不能改写成null/error而丢失已生成答案。取消/异常没有完整结果时保存null answer及安全错误标记。进程崩溃或存储持续故障可能留下generating，后台显示“尚未确认”，不能推断为已答。

v3固定Redis命名空间`ask-ops:{v3}:`，原子记录和索引均无TTL；手动删除同时移除问答、来源、会话关联和指标，并保留24小时无正文tombstone防止延迟重试复活。公开接口没有问答读取权限。私人Ops由个人站验证owner邮箱后读取、导出和单条删除。无新提示的旧客户端仍只执行已披露的v1规则，不保存完整回答。

## v1 历史协议（30天）

用户明确要求保存提问，用于观察大家关心的问题、查找回答不足。默认模型同时改为Builder提供的`deepseek-v4-flash`。这项记录与身份和额度账本分开。

新页面在输入框旁显示「提问会保存30天，用于改进回答。请勿填写私密信息。」并在请求中附`query_log_notice: "v1"`。后台只记录已通过额度准入、带这项提示版本的有效提问。旧页面、无提示的调用、输入验证失败、超额或被限流的请求不记录。

每条记录使用随机UUID，仅含question、created_at、model、status、duration_ms。状态覆盖answered、clarify、unsupported、sources-only、error、cancelled，便于定位失败和停止的请求；不保存补充背景、聊天历史、完整回答、原始模型思考、邮箱、IP、账号ID或额度subject。问题文本本身可能包含用户自行输入的信息，所以页面提示不要填写私密内容；不将其称为已匿名化资料。

Builder在请求清理时将记录投入有容量上限的后台写入队列，记录失败不影响回答、扣次或释放并发槽。不在SSE首段或最终回答之前等待存储，也不把问题或异常详情写进运行日志；因此服务重启、队列满或存储故障可能丢失个别记录。这是改进回答所用的尽力记录，不是完整审计日志。

两边均须显式设置`ASK_QUERY_LOG_ENABLED=true`；Builder还须启用额度。数据库凭证仍只在Vercel。Builder使用已注入token按quota-store用途派生的密钥，通过固定HTTPS端点`POST https://www.lizheng.ai/api/ask-lizheng/query-storage`传输；使用独立`X-Ask-Query-Proof`头和`ask-query-store:v1`签名前缀，不能混用额度证明。正文为有大小上限的compact UTF-8 JSON，Content-Type为application/octet-stream。Node验证签名、45秒证明、完整字段白名单、问题长度、模型、状态和时间；只运行固定Lua，不提供通用Redis代理。

Redis键为`ask-query:v1:<UUID>`，HASH仅包含五个批准字段，过期时间为提问时间加30天。幂等重试既不覆盖首条记录，也不延长保留期。公开端点只允许受保护写入，没有公开读取路由。

读取工具位于个人站仓库`scripts/read-ask-query-records.ts`：

```sh
pnpm exec tsx scripts/read-ask-query-records.ts --limit 20
pnpm exec tsx scripts/read-ask-query-records.ts --limit 20 --include-question
```

只能在owner本机进程中提供已有服务端Redis配置，不能放在网页、参数或源码里。默认只读取时间、模型、状态和耗时；明确使用`--include-question`才读问题正文。最多返回100条，扫描达到边界会标记truncated；输出是私有用户输入，不应贴到公开仓库、页面或日志。自动过期仅约束服务内记录；不要长期导出正文到别处。

发布先使新页面提示和受保护存储端点就绪，再启用记录开关；旧客户端不带提示版本，继续不记录。关闭记录开关可停止新增记录，既有条目按原期限过期；此开关不关闭账号、额度或问答。
