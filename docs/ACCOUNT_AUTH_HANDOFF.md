# 问问立正身份与额度配置

此为2026-10-01版本的配置与验收合同。主路径复用咖啡馆的邮箱验证码，不依赖新Logto应用。主页架子已在线，身份与额度须按以下步骤协调启用，规则见[ACCOUNT_QUOTAS.md](ACCOUNT_QUOTAS.md)。

## 已有方法与配置来源

咖啡馆已使用Resend发送邮箱验证码、Redis原子核验，然后按已验证邮箱读取Circle Founding Member标签271455。Ask采用同一路径：邮箱所有权验证 → Circle精确查询 → Founding不限次。未核验为Founding仍每天3次，登录不重置访客额度。

现有Coffee生产Redis配置已通过连通核验；原领书会话引用的Resend发送及Circle凭证源也已定位。Resend是受限发送key，不能用读取domains的401判断邮件发送失败。凭证只在Vercel服务端安全读取和配置，不写入代码、聊天、截图或本文。Ask使用独立namespace，不改领书或咖啡领取记录。认证加密密钥新生成；准入与额度调用密钥从平台注入token按固定独立用途派生，不沿用Coffee密钥。

## 部署配置

| 位置 | 环境变量 | 内容来源或用途 |
| --- | --- | --- |
| 个人站Vercel及Ask Builder | ASK_QUOTA_ENABLED | 两端验收就绪后协调设true；默认关闭 |
| 个人站Vercel | ASK_ADMISSION_SECRET | HMAC-SHA256(token, `ask-lizheng:admission:v1`)所得hex字符串；Builder同法自动派生 |
| 个人站Vercel | ASK_QUOTA_STORE_SECRET | HMAC-SHA256(token, `ask-lizheng:quota-store:v1`)所得hex字符串；只用于额度操作签名 |
| 个人站Vercel | ASK_AUTH_SECRET | 独立新生成至少32字节密钥；认证加密与HMAC |
| 个人站Vercel | ASK_AUTH_EMAIL_API_KEY | 现有受控Resend发送凭证 |
| 个人站Vercel | ASK_CIRCLE_ADMIN_V2_TOKEN | 现有Circle Admin V2只读查询凭证 |
| 个人站Vercel | ASK_AUTH_REDIS_REST_URL／ASK_AUTH_REDIS_REST_TOKEN | Coffee生产受控Upstash REST配置；独立Ask身份namespace |
| Ask Builder | ASK_QUOTA_STORE_ORIGIN | 固定公开URL `https://www.lizheng.ai/api/ask-lizheng/quota-storage` |

Builder无需额外Redis或邮件密钥。额度代理仅接受签名的固定Lua脚本、3个Ask额度key及有界参数，拒绝通用Redis操作。token轮换时同步更新Vercel两个派生密钥；配置缺失或不一致时关闭生成。本地测试仍可显式使用独立Redis配置。

邮件固定由`立正 <podcast@notify.lizheng.ai>`发送，Reply-To为`sunyuzheng@gmail.com`；只包含问问立正验证码、有效期和忽略说明，不包含问题、对话、会员状态或材料。

验证码10分钟有效，最多5次尝试，成功一次性消费。发送和重发按邮箱HMAC、网络入口HMAC及全局桶限速。原始IP不保存；短期邮箱记录加密，验证码只保留HMAC。

## 两个入口及可选SSO

主页与ask.lizheng.ai同源身份接口使用固定生产origin。优先在验证窗口完成；返回只刷新身份，不自动提交问题。浏览器拦截窗口时暂存未发送草稿10分钟，返回即删。

已写的Academy SSO可保留为可选入口，真实专用配置未定位前不影响邮件验证。回查原会话确认鸭哥创建的是Story Coffee Pass；Ask不会用Coffee client secret冒充独立应用。若以后启用专用Ask SSO，准确回调为：

```text
https://ask.lizheng.ai/api/ask-lizheng/auth/callback
https://www.lizheng.ai/api/ask-lizheng/auth/callback
```

## 验收与发布边界

先完成本地与受控验证：邮件发码／一次消费、错误码及重发限速、Founding与非Founding、两入口额度、停止／失败退款和响应开始前断线清理。实际额度链路使用随机合成subject，验证后清理精确测试key。用签名`GET /api/quota`确认实际存储；`/health`仅证明公开资料就绪。

发布先使Vercel认证与受保护额度端点就绪，再更新Builder流式版本，验收邮件和存储，最后协调两个开关启用。只更改问问立正相关路由与配置，保留个人站main上已有设计。任一必要核验失败时保持额度关闭，先交付流式改进。部署后检查模型、完整SSE结果、匿名额度与Founding会话。

生产配置与代码部署仍需展示准确diff、源配置引用、目的地及访客范围后取得发布批准。此前待审阅的逐段回答与持续等待反馈也属于本批代码变更。合成验收不等于真实身份已上线。

2026-10-01已按获批差异发布：Builder main 2385b57、个人站main 156d1a8。邮箱验证码真实投递、成功核验及一次消费检查通过；生产会话、双入口访客三次及第四次拒绝通过。生产CLI通过stdin配置值时必须传精确值，不能额外追加换行；敏感值不回显。需要分阶段切换时，可先用Vercel --prod --skip-domain构建，再在Builder就绪后promote，减少开关不同步时间。新增提问记录启用与默认Flash变更另见[QUERY_RECORDS.md](QUERY_RECORDS.md)。
