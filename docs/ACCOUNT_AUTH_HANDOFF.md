# 问问立正身份与额度配置

状态：本地候选，尚未配置或发布。用户最新决定先复用咖啡馆的邮箱验证码路径，不依赖新Logto应用。主页架子已交付至个人站本地工作区，规则见[ACCOUNT_QUOTAS.md](ACCOUNT_QUOTAS.md)。

## 已有方法与配置来源

咖啡馆已使用Resend发送邮箱验证码、Redis原子核验，然后按已验证邮箱读取Circle Founding Member标签271455。Ask采用同一路径：邮箱所有权验证 → Circle精确查询 → Founding不限次。未核验为Founding仍每天3次，登录不重置访客额度。

现有Coffee生产Redis配置已找到并通过只读PING；原领书会话引用的本机Resend及Circle凭证源也已定位。凭证只在服务端安全读取和部署，不写入代码、聊天、截图或本交接稿。Ask使用独立namespace，不改领书或咖啡领取记录；认证与请求签名密钥重新生成，不沿用Coffee密钥。

## 部署配置

| 位置 | 环境变量 | 内容来源或用途 |
| --- | --- | --- |
| 个人站Vercel及Ask Builder | ASK_QUOTA_ENABLED | 两端验收就绪后协调设true；默认关闭 |
| 个人站Vercel及Ask Builder | ASK_ADMISSION_SECRET | 相同的新生成至少32字节密钥；签名绑定身份和本次请求 |
| 个人站Vercel | ASK_AUTH_SECRET | 独立新生成至少32字节密钥；认证加密与HMAC |
| 个人站Vercel | ASK_AUTH_EMAIL_API_KEY | 现有受控Resend发送凭证 |
| 个人站Vercel | ASK_CIRCLE_ADMIN_V2_TOKEN | 现有Circle Admin V2只读查询凭证 |
| 个人站Vercel | ASK_AUTH_REDIS_REST_URL／ASK_AUTH_REDIS_REST_TOKEN | Coffee生产受控Upstash REST配置；独立Ask身份namespace |
| Ask Builder | ASK_QUOTA_REDIS_REST_URL／ASK_QUOTA_REDIS_REST_TOKEN | 同一受控Redis；独立Ask额度namespace |

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

先完成本地和受控环境验证：邮件发码／一次消费、错误码及重发限速、Founding与非Founding、两入口额度、停止／失败退款和响应开始前断线清理。用签名`GET /api/quota`确认实际存储；`/health`仅证明公开资料就绪。

生产配置与代码部署仍需展示准确diff、源配置引用、目的地及访客范围后取得发布批准。此前待审阅的逐段回答与持续等待反馈也属于本批代码变更。合成验收不等于真实身份已上线。
