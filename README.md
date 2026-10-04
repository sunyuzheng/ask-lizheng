# 问问立正

卡住的时候，问问立正：从立正六年的视频（含会员视频）、文章和《真本事》课程里找出处，整理成回答。这里保存完整前端、检索与模型后端，以及 Builder Space 的部署入口。

## 使用体验

首页提供「想明白」「从哪读起」两种问法和六个可修改的预填问题。「想明白」解释观点（intent understand）；打开「结合我的处境」后可填目标、现状与卡点、做过的尝试（都可留空），回答会落到用户的处境上（intent apply），关闭时不发送这些内容。「从哪读起」挑出最值得先读的原文并说明理由（intent find）；知道标题或关键词时，lizheng.ai主页的站内搜索更快。回答先解释核心判断，再根据问题展开；必要时只追问一两个会影响结论的条件。后续问题会带上本次对话的必要上下文重新检索。

完整回答可保存为长图或A4 PDF，带问题、回答、出处和回到ask.lizheng.ai的二维码，不含用户填写的处境。每份推荐材料保留原始标题、日期、作者、片段与视频时间点。回答会标明 AI 综合或结合个人处境的应用。没有材料支持、模型未连接或暂时故障时，产品会说明状态，并提供确实找到的材料。

浏览器对话仅在内存中，刷新清除。旧v1保存提示对应的记录仍在30天后自动删除。应用户新要求，启用ops后，已展示v3问答保存提示的新页面会在生成前可靠保存已受理的问题，在发送完整回答前确认保存当次完整回答和所用来源，并记录匿名使用标识、对话分组与轮次、问题字数、提问方式和入口、时间、模型、回答状态及耗时，直到站点所有者手动删除。不关联邮箱、账号或IP，不单独保存补充背景、提交的历史摘要或模型内部推理；归档回答可能引用输入背景。无公开记录读取接口，协议见[提问记录说明](docs/QUERY_RECORDS.md)。可用邮箱验证码核验Superlinear Founding身份：所有人每天3次，Founding Member每日不限次；配置默认关闭，身份与额度元数据只在服务端保存。输入与选出的公开材料发送给 Builder Space 的模型服务，其处理规则由该服务管理。它不是立正本人实时回复。

## 本地运行

```sh
npm ci
npm run build
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 在进程环境设置 AI_BUILDER_TOKEN；不设置时仍可检索资料。
.venv/bin/python -m server.app
```

打开 http://127.0.0.1:8000 。前端开发可单独运行 `npm run dev`，Vite 将 API 转发到 8000 端口。

## 公开资料

`data/context` 是 [lizheng-open-context](https://github.com/sunyuzheng/lizheng-open-context) 的哈希校验副本；它的 release manifest 和许可一起保留，版本绑定在 `data/context-lock.json`。本产品不读取个人私有资料。更新时先在上游完成内容审阅和 release validation，再运行：

```sh
python3 scripts/sync_context.py --source /path/to/lizheng-open-context
# 先查看新的公开索引范围；确认后通过进程环境提供 token 再构建。
.venv/bin/python scripts/build_semantic_index.py
.venv/bin/python scripts/build_semantic_index.py --build
```

该脚本只复制 release manifest 中列出的公开文件，不复制 `.source-cache`、Git、凭证或上游运行数据。回答时用词法与语义候选、概念别名和子问题覆盖选择不同 source family；8 组带原文的判断卡补入条件、边界和关联材料。把原始出处与归属随片段交给模型。模型只能选择来源编号，URL、摘录、日期与视频时间点由服务器提供。译文、转载与原作不重复算独立证据。

## API 与部署

`GET /api/meta` 返回材料版本、模型与推理设置；`GET /api/search?q=...` 返回公开出处；`POST /api/ask` 接受 `question`、`context`、`intent` 与最多六轮 `history`，通过 SSE 先返回真实处理进度、候选sources与公开整理方向，再逐段显示已经过出处和格式校验的内容，最后返回完整result。接口对输入长度、并发与频率设限；问答正文不进入应用日志。

Builder Space 模型接口为 `https://space.ai-builders.com/backend/v1/chat/completions`，默认 `deepseek-v4-flash`，附带 thinking enabled、`reasoning_effort: low`、JSON object 与同一 schema 提示；流式段落和最终回答继续经过格式与来源验证，并至多修复一次。可通过 `AI_MODEL` 更换：`grok-4.5` 使用 medium，`gpt-5` 使用 low，`deepseek-v4-pro` 使用 high。根目录 Dockerfile 将 Vite 静态文件与 FastAPI 放进同一进程、同一端口，遵守 `PORT`。平台从部署者账号自动注入 `AI_BUILDER_TOKEN`，无需把密钥放入部署 payload。当前平台要求公开 GitHub 仓库，并提供 256 MB 容器；具体契约见[官方 OpenAPI](https://space.ai-builders.com/backend/openapi.json)。服务闲置5分钟后会深度休眠，下一个请求把它唤醒，通常要几秒到半分钟。ask.lizheng.ai根路径因此先经个人站的页面函数：服务1.5秒内回应就原样给页面，否则先显示「正在唤醒」并在醒来后自动刷新；页面里读次数和提问等得久时，也会说明服务在唤醒。

部署脚本默认dry-run，列出目的地、完整非秘密payload与审批摘要。审阅时传入`--expected-commit`的完整Git SHA；正式部署再传对应`--approved-sha`。启用额度时加`--enable-quota`，只向Builder传公开开关与固定额度代理URL，数据库、Circle及邮件凭证都留在个人站Vercel。脚本会先验证公开仓库main正是该版本，再读取模型token并执行部署。发布仍需取得对准确差异与目的地的批准。完整调用契约见[API.md](docs/API.md)。

## 验证

```sh
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
npm run build
# 设置模型 token 后，使用合成问题进行真实模型核验：
.venv/bin/python -m server.live_eval
```

产品设计、Context 架构与验收记录见 `docs/PRODUCT.md`、`docs/CONTEXT_ARCHITECTURE.md` 和 `docs/VERIFICATION.md`。版权见 [`LICENSE.md`](LICENSE.md)：程序和文档是 MIT；`data/context` 里的资料沿用 Open Context 的许可，不随 MIT 授权。

Superlinear账号与每日3次／Founding Member不限次的实现说明见[ACCOUNT_QUOTAS.md](docs/ACCOUNT_QUOTAS.md)，邮箱验证及服务端发布配置见[ACCOUNT_AUTH_HANDOFF.md](docs/ACCOUNT_AUTH_HANDOFF.md)。按配置交接中的验收步骤协调启用两个入口。
