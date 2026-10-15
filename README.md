# 问问立正

卡住的时候，问问立正：依据立正的文章、主讲与可归属的对谈发言解释问题。「从哪读起」检索整个资料库，查找嘉宾节目、原稿和阅读材料。这里保存完整前端、检索与模型后端，以及 Builder Space 的部署入口。

## 使用体验

首页提供「想明白」「从哪读起」两种问法和六个可修改的预填问题。「想明白」解释观点（intent understand）；打开「结合我的处境」后可填目标、现状与卡点、做过的尝试（都可留空），回答会落到用户的处境上（intent apply），关闭时不发送这些内容。「从哪读起」挑出最值得先读的原文并说明理由（intent find），按嘉宾查询时直接展示相关节目和原文片段；知道标题或关键词时，lizheng.ai主页的站内搜索更快。回答先解释核心判断，再根据问题展开；必要时只追问一两个会影响结论的条件。后续问题会带上本次对话的必要上下文重新检索。

每个完整回答存档时就有自己的网页 `ask.lizheng.ai/s/<提问日期>/<英文词>`，地址栏随之换成它，刷新不丢，打开就是同一页、能接着问（「结合我的处境」的回答由提问人点了分享才建）。复制、发送或打开二维码算一次分享，当天多一次提问机会（每天一次，微信和App里不提供）。长图和A4 PDF带问题、回答、材料地图、出处和这条回答网页的二维码，不含处境。每份推荐材料保留原始标题、日期、作者、片段与视频时间点；只对会员开放的出处标金色。回答会标明 AI 综合或结合个人处境的应用。没有材料支持、模型未连接或暂时故障时，产品会说明状态，并提供确实找到的材料；等待和出错时可以直接「反馈问题」。

这里像在讲座上举手提问：问答会公开，但不记名。问题、完整回答和所用出处在生成前后可靠存档，直到立正删除；不关联邮箱、账号或IP，不保存「结合我的处境」的原文、历史摘要或模型内部推理（回答可能提到处境里的细节）。每个回答过的问题都原样放进「别人在问什么」，开了「结合我的处境」的和接着问的也一样（2026-10-15起；之前由AI挑选）。本机的「我的提问」只存在这台设备上。存档、公开和分享的约定见[提问记录说明](docs/QUERY_RECORDS.md)；旧v1提问记录按当时说的30天到期。可用邮箱验证码核验Superlinear Founding身份：所有人每天3次，Founding Member每日不限次，身份与额度元数据只在服务端保存。输入与选出的公开材料经 Builder Space 发给模型服务：DeepSeek 写回答（它30秒内没开始写时改由 OpenAI 的 gpt-5），OpenAI 匹配资料。它不是立正本人实时回复。

## 本地运行

```sh
npm ci
npm run build
node --test tests/*.mjs   # 页面自己的模块：主页交来的问题、出处归属
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 在进程环境设置 AI_BUILDER_TOKEN；不设置时仍可检索资料。
.venv/bin/python -m server.app
```

打开 http://127.0.0.1:8000 。前端开发可单独运行 `npm run dev`，Vite 将 API 转发到 8000 端口。

## 公开资料

`data/context` 是 [lizheng-open-context](https://github.com/sunyuzheng/lizheng-open-context) 的哈希校验副本；它的 release manifest 和许可一起保留，版本绑定在 `data/context-lock.json`。人物和节目目录、原稿与AI提炼都由该仓库维护，详见[Context架构](docs/CONTEXT_ARCHITECTURE.md)。

服务不把这些资料放在内存里（2026-10-15起）：构建镜像时 `python -m server.build_store` 把它们建成本地数据库文件 `data/store/ask.sqlite`（不进 Git），里面是每段正文和「每个词出现在哪些段落」的索引；回答时查索引找候选，只读出用到的段落。本地第一次加载真实资料时会自动建一份（约半分钟），资料、语义索引或建索引的代码一变就重建。

Open Context 推到 main 就会自动上线（2026-10-04 起，立正的决定：推 main 就是批准它的内容进入问问立正）。`.github/workflows/update-context.yml` 每半小时比较一次，有新版本就运行 `scripts/update_context.py`：先跑 Open Context 自己的 release validation 和测试，再同步副本、重建语义索引、跑本仓库测试，只把 `data/` 提交到 main，用 `config/builder-deploy.json` 的线上设置部署 Builder，等线上 `/api/meta` 的 `context_release` 变成新版本、语义索引就绪，再把 `context-live` 标签移到这个提交。任何一步失败就停下，这次运行失败，GitHub 发邮件给立正；部署没完成的，下一轮会再部署。想马上更新就运行 `gh workflow run update-context.yml -R sunyuzheng/ask-lizheng`。部署令牌存在仓库的 `builder` 环境里（只有 main 上的运行能读），换令牌时要一起换。

索引建好后，`scripts/build_answer_map.py` 用它和语料重排材料地图（`data/answer-map.json`，回答服务在 `/api/answer-map` 提供给页面），几秒钟、不调用模型；地图和当前资料版本对不上时测试不通过。

重建索引只为新的或改过的文字窗口调用 embedding，其余向量按文字哈希原样保留；向量文件固定叫 `vectors.f32`，所以 Git 只多存变化的部分。手动做同样的事（例如排查）：

```sh
python3 scripts/sync_context.py --source /path/to/lizheng-open-context
# 先看有多少窗口要重新计算；--build 需要进程环境里的 AI_BUILDER_TOKEN，--full 全部重算。
.venv/bin/python scripts/build_semantic_index.py
.venv/bin/python scripts/build_semantic_index.py --build
```

该脚本只复制 release manifest 中列出的公开文件，不复制 `.source-cache`、Git、凭证或上游运行数据。回答时用词法与语义候选、概念别名和子问题覆盖选择不同 source family；29组带原文的判断卡补入条件、边界和关联材料。把原始出处与归属随片段交给模型。模型只能选择来源编号，URL、摘录、日期与视频时间点由服务器提供。译文、转载与原作不重复算独立证据。

## API 与部署

`GET /api/meta` 返回材料版本、模型与推理设置；`GET /api/search?q=...` 返回公开出处；`POST /api/ask` 接受 `question`、`context`、`intent` 与最多六轮 `history`，通过 SSE 先返回真实处理进度、候选sources与公开整理方向，再逐段显示已经过出处和格式校验的内容，最后返回完整result。接口对输入长度、并发与频率设限；问答正文不进入应用日志。

Builder Space 模型接口为 `https://space.ai-builders.com/backend/v1/chat/completions`，默认 `deepseek-v4-flash`，附带 thinking enabled、`reasoning_effort: low`、JSON object 与同一 schema 提示；流式段落和最终回答继续经过格式与来源验证，并至多修复一次。可通过 `AI_MODEL` 更换：`grok-4.5` 使用 medium，`gpt-5` 使用 low，`deepseek-v4-pro` 使用 high。DeepSeek 30秒内没开始写回答或先报错时，同一预算内改由备用模型 `gpt-5` 重答一次（`server/answers.py` 的 `BACKUP_MODELS`，`/api/meta` 的 `backup_model`）；页面说明、App 的同意和隐私政策都写明了它。根目录 Dockerfile 将 Vite 静态文件与 FastAPI 放进同一进程、同一端口，遵守 `PORT`，并在构建时建好资料索引（镜像里 `ASK_STORE=prebuilt`，服务启动时不再自己建）。平台从部署者账号自动注入 `AI_BUILDER_TOKEN`，无需把密钥放入部署 payload。当前平台要求公开 GitHub 仓库，并提供 256 MB 容器；具体契约见[官方 OpenAPI](https://space.ai-builders.com/backend/openapi.json)。服务闲置后会休眠，下一个请求把它唤醒。ask.lizheng.ai 的页面文件由个人站托管，打开即显示；页面读 `/api/meta` 最多等一分钟，3秒没回应就说明服务在唤醒，醒来前不能发送。提问经个人站的 Edge 函数转发，12秒内没收到这里的回应就先给页面回话、边等边发心跳（Vercel 要求25秒内开始回话）。

部署脚本默认dry-run，列出目的地、完整非秘密payload与审批摘要。审阅时传入`--expected-commit`的完整Git SHA；正式部署再传对应`--approved-sha`。启用额度时加`--enable-quota`，只向Builder传公开开关与固定额度代理URL，数据库、Circle及邮件凭证都留在个人站Vercel。`--from-settings` 改用 `config/builder-deploy.json` 里的线上设置（目前等于 `--enable-quota --enable-query-log --enable-ops --model deepseek-v4-flash`）；改线上设置时要同时改这个文件，自动更新只按它部署。脚本会先验证公开仓库main正是该版本，再读取模型token，确认 lizheng.ai 持有由这个 token 派生的密钥（否则所有签名调用都会失败，脚本不部署），然后执行部署。发布仍需取得对准确差异与目的地的批准；Open Context 资料的自动更新是唯一例外，见上文。完整调用契约见[API.md](docs/API.md)。

## 验证

```sh
.venv/bin/pip install pytest 'fakeredis[lua]'
.venv/bin/python -m pytest -q tests
npm run build
# 镜像在256MB里能启动、服务进程最高内存不超过200MB（CI 和资料自动更新都跑）：
docker build -t ask-lizheng . && python scripts/check_image_memory.py ask-lizheng
# 设置模型 token 后，使用合成问题进行真实模型核验：
.venv/bin/python -m server.live_eval
```

产品设计、Context 架构与验收记录见 `docs/PRODUCT.md`、`docs/CONTEXT_ARCHITECTURE.md` 和 `docs/VERIFICATION.md`；本仓库、个人站和私人后台怎样配合见 `docs/ARCHITECTURE.md`。版权见 [`LICENSE.md`](LICENSE.md)：程序和文档是 MIT；`data/context` 里的资料沿用 Open Context 的许可，不随 MIT 授权。

Superlinear账号与每日3次／Founding Member不限次的实现说明见[ACCOUNT_QUOTAS.md](docs/ACCOUNT_QUOTAS.md)，邮箱验证及服务端发布配置见[ACCOUNT_AUTH_HANDOFF.md](docs/ACCOUNT_AUTH_HANDOFF.md)。按配置交接中的验收步骤协调启用两个入口。
