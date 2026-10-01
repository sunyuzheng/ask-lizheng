# 提问记录

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
