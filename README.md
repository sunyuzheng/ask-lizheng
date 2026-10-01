# 问问立正

从立正公开的文章、视频与《真本事》出发，找到出处，把一个问题想得更明白。这里保存完整前端、检索与模型后端，以及 Builder Space 的部署入口。

## 使用体验

首页提供「想明白」「聊聊我的问题」「找内容」三个入口，以及六个可修改的预填问题。背景补充是选填：目标、事实与限制、做过的尝试。回答先解释核心判断，再根据问题展开；必要时只追问一两个会影响结论的条件。后续问题会带上本次对话的必要上下文重新检索。

每份推荐材料保留原始标题、日期、作者、片段与视频时间点。回答会标明 AI 综合或结合个人处境的应用。没有材料支持、模型未连接或暂时故障时，产品会说明状态，并提供确实找到的材料。

本项目不建立对话数据库或分析埋点；浏览器对话仅在内存中，刷新清除。可配置Superlinear SSO：所有人每天3次，核验为Founding Member后每日不限次；默认关闭，身份与额度元数据只在服务端保存。输入与选出的公开材料发送给 Builder Space 的模型服务，其处理规则由该服务管理。它不是立正本人实时回复。

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

`GET /api/meta` 返回材料版本、模型与推理设置；`GET /api/search?q=...` 返回公开出处；`POST /api/ask` 接受 `question`、`context`、`intent` 与最多六轮 `history`，通过 SSE 先返回真实处理进度和候选 sources，再返回完整且通过格式与引用校验的 result。接口对输入长度、并发与频率设限；问答正文不进入应用日志。

Builder Space 模型接口为 `https://space.ai-builders.com/backend/v1/chat/completions`，默认 `grok-4.5`，附带 `reasoning_effort: medium`，可通过 `AI_MODEL` 更换。`gpt-5` 使用 low；`deepseek-v4-pro` 使用 JSON object 与同一 schema 提示、thinking enabled / high，并保留服务端格式、来源验证和一次修复。根目录 Dockerfile 将 Vite 静态文件与 FastAPI 放进同一进程、同一端口，遵守 `PORT`。平台从部署者账号自动注入 `AI_BUILDER_TOKEN`，无需把密钥放入部署 payload。当前平台要求公开 GitHub 仓库，并提供 256 MB 容器；具体契约见[官方 OpenAPI](https://space.ai-builders.com/backend/openapi.json)。

部署脚本默认 dry-run，列出目的地、完整非秘密 payload 与审批摘要。审阅时传入 `--expected-commit` 的完整 Git SHA；正式部署再传对应 `--approved-sha`。脚本会先验证公开仓库的 main 正是该版本，再读取模型 token 并执行部署。首次发布前仍需得到用户对公开仓库和服务目的地的明确批准。完整调用契约见 [API.md](docs/API.md)。

## 验证

```sh
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
npm run build
# 设置模型 token 后，使用合成问题进行真实模型核验：
.venv/bin/python -m server.live_eval
```

产品设计、Context 架构与验收记录见 `docs/PRODUCT.md`、`docs/CONTEXT_ARCHITECTURE.md` 和 `docs/VERIFICATION.md`。源内容遵守原有许可证；项目代码为 MIT。

Superlinear账号与每日3次／Founding Member不限次的实现说明见 [ACCOUNT_QUOTAS.md](docs/ACCOUNT_QUOTAS.md)，专用SSO应用与服务端配置需求见 [ACCOUNT_AUTH_HANDOFF.md](docs/ACCOUNT_AUTH_HANDOFF.md)。本地实现已完成，尚未启用或上线每日额度。
