# AI4S Daily

AI4S Daily 是一个面向 AI for Science 的自托管情报流水线。它抓取公开来源，用 DeepSeek 判断 AI4S 相关性并生成结构化科研摘要，再将结果固化为 Daily / Weekly Report，最后渲染成可由 GitHub Pages 托管的静态站点。

当前 AI4S 开发位于 `feat/ai4s-redesign`。`main` 和 `baseline-ai-daily` 保留原版稳定基线。

## Architecture

```text
Sources
  → Fetch / Item
  → AnalyzerResult
  → AI4SSummary
  → Daily Report / Weekly Report
  → render-ai4s
  → static HTML
  → GitHub Pages
```

- Fetch 只保存来源返回的公开内容，并按 URL 去重。
- Analyzer 同时完成 AI4S 判定、领域分类、内容类型识别和 0–10 分评分。
- Summarizer 只处理达到阈值的 AI4S 内容，输出科学问题、AI 方法、主要结果、创新点、科研意义和资源。
- Report Layer 固化日报和周报快照；前端只读取 Report Layer，不重新筛选旧摘要。
- SQLite 是唯一持久层。开发与生产分别使用独立数据库。

## Windows Quickstart

以下命令均在仓库根目录执行。不要把 API Key 写进仓库、配置文件或命令历史。

```powershell
git clone https://github.com/tastysoup-s/aifors-daily.git D:\project\ai-daily
Set-Location D:\project\ai-daily
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

在 Windows 系统环境变量中设置 `DEEPSEEK_API_KEY`。如果是在 Codex 或终端启动后新增的变量，请重启 Codex 或终端再继续。

开发流水线必须显式使用 `data/ai4s_dev.db`：

```powershell
.\.venv\Scripts\python.exe -m src.main fetch --db data/ai4s_dev.db
.\.venv\Scripts\python.exe -m src.main analyze --db data/ai4s_dev.db
.\.venv\Scripts\python.exe -m src.main summarize-ai4s --db data/ai4s_dev.db
.\.venv\Scripts\python.exe -m src.main generate-daily --db data/ai4s_dev.db
.\.venv\Scripts\python.exe -m src.main generate-weekly --db data/ai4s_dev.db
.\.venv\Scripts\python.exe -m src.main render-ai4s --db data/ai4s_dev.db --output-dir site
.\.venv\Scripts\python.exe -m http.server 8000 -d site
```

然后访问 <http://localhost:8000>。

调试真实 LLM 调用时可以用 `--limit` 控制范围：

```powershell
.\.venv\Scripts\python.exe -m src.main analyze --db data/ai4s_dev.db --limit 3
.\.venv\Scripts\python.exe -m src.main summarize-ai4s --db data/ai4s_dev.db --limit 2
```

`data/ai_daily.db` 是原版/历史参考数据库，不用于 AI4S 实验。CLI 默认数据库路径为该历史路径，因此 AI4S 命令应始终显式传入 `--db data/ai4s_dev.db`。

## Daily and Weekly Reports

日报按 UTC 自然日选择已经完成 AI4S 摘要的内容，不调用 LLM：

```powershell
.\.venv\Scripts\python.exe -m src.main generate-daily --db data/ai4s_dev.db --report-date 2026-09-03
```

周报每周三、周日生成，每次覆盖截至报告日的最近七天（UTC，以条目发表时间为准）。周报会进行一次趋势综合 LLM 调用；相同时间窗重复执行会复用已保存报告，不再次调用模型。

```powershell
.\.venv\Scripts\python.exe -m src.main generate-weekly --db data/ai4s_dev.db --report-date 2026-09-06
```

## Frontend

`render-ai4s` 读取最新 Daily / Weekly Report 并生成单文件 UTF-8 页面：

- Daily / Weekly 切换；
- All、Biology、Medicine、Chemistry、Materials、Earth、Physics、General 分类过滤；
- Daily 展示完整六字段科研摘要、分数、标签、来源和发布时间；
- Weekly 展示总体趋势、分类趋势、观察清单和代表工作；
- 提供无报告、空报告、筛选无结果状态；
- 支持桌面与移动端，不依赖外部前端框架或静态资源。

生成文件默认为 `site/index.html`；自动化发布时生成到 `docs/index.html`。

## GitHub Actions

工作流位于 `.github/workflows/daily.yml`：

- 每天 UTC 00:30 运行一次，也支持手动触发；
- 顺序固定为 Fetch → Analyze → Summarize AI4S → Daily → 条件 Weekly → Render；
- 周三和周日 UTC 生成 Weekly Report；
- 生产数据库固定为 `data/ai4s.db`；
- 只恢复和持久化该生产数据库，不混用 `data/ai_daily.db` 或 `data/ai4s_dev.db`；
- 数据库与 `docs/` 发布产物写入独立 `data` 分支；
- LLM 仅使用 Repository Secret `DEEPSEEK_API_KEY`；Git 推送使用 Actions 自带的 `GITHUB_TOKEN`。

定时工作流只有出现在仓库默认分支后才会自动触发。在功能分支验证期间可使用 `workflow_dispatch`，不要为启用定时任务而直接改写稳定 `main`。

## GitHub Pages

第一次成功运行工作流后会创建或更新 `data` 分支。随后在仓库中设置：

1. `Settings` → `Pages`。
2. `Build and deployment` 的 Source 选择 `Deploy from a branch`。
3. Branch 选择 `data`，folder 选择 `/docs`。
4. 保存并等待 GitHub Pages 发布。

本仓库预期地址为 <https://tastysoup-s.github.io/aifors-daily/>。如果仍为 404，先确认 `data` 分支已存在、其中有 `docs/index.html`，再确认 Pages 分支与目录设置。

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试覆盖 Fetch、Analyzer、AI4S Summarizer、Report Layer、静态前端、CLI 和 Workflow 配置。真实 API 验证应使用小 `--limit`，并在报告中记录调用数、错误隔离和成本。

## State and Safety

- `.env`、`.venv/`、`data/*.db` 和本地 `site/` 均不得提交。
- `data/ai4s_dev.db` 只用于本地开发。
- `data/ai4s.db` 只用于自动化生产。
- `data/ai_daily.db` 只作为原版/历史参考。
- `data` 分支是公开仓库中的机器生成状态；其中的 SQLite 和 HTML 也会公开。
- 摘要字段校验失败时，该条不会写入完成状态，其余条目继续处理。
- Report 是不可变时间窗快照；重复生成同一时间窗会复用已有结果。

## Troubleshooting

- `DEEPSEEK_API_KEY` 读不到：如果刚配置系统变量，请重启 Codex 或终端；只检查变量是否存在，不要输出完整值。
- Analyzer 没有候选：确认 Fetch 已写入同一个 `--db` 路径，并检查抓取窗口。
- Daily 为空：确认候选已通过 Analyzer 阈值且 `summarized_at` 已写入。
- Weekly 日期报错：显式日期必须是 UTC 周三或周日。
- 页面为空：先生成报告，再运行 `render-ai4s`；渲染器不会直接读取未进入 Report 的摘要。
- Pages 404：确认 `data` 分支、`docs/index.html` 和 Pages 的 `data` + `/docs` 设置。
- 本地端口占用：将 `http.server` 的 `8000` 换成其他端口。
- 个别来源正文很短：当前只使用来源响应中已有的内容，不额外抓落地页、README 或 PDF。

旧版 `summarize`、`render`、`summaries` 表和历史模板仍保留用于 baseline 兼容；AI4S 流水线使用 `analyze`、`summarize-ai4s`、Report Layer 和 `render-ai4s`。

## License

MIT. 详见 [LICENSE](./LICENSE)。


## AI4S-Daily V2 intelligence

- Fetch 广泛发现候选；Analyzer 独立判断 AI4S、科学领域与价值，检索 hint 不决定分类。
- arXiv 科学领域源支持可选 `terms`：`(categories OR ...) AND (all:"method" OR ...)`；旧配置不变。
- Daily 保持 AI4S、评分和 information sufficiency 门槛，再按 `ceil(top_n * 0.30)` 软领域上限选择；候选不足按原排名回填，同质量保留来源多样性。
- 合格池允许时，Daily 至少保留 30% 位置给非 Biology/Medicine，且覆盖至少两个非生物医学领域；不足时才回填。
- 摘要按领域轮流读取高分新候选，直到得到 `top_n` 条信息充分摘要或候选耗尽。单次最多读取 `5 * top_n` 条（默认 50），不重试同一候选；显式 `--limit` 仍为调用硬上限。已有摘要不重做。
- 新摘要事实字段约 70/100 字，研判 40–90 中文字、一个关键判断。超长输出报单条错误，不截断事实或自动增加 LLM 调用。旧摘要及已保存报告不自动重写。
- Daily 无图片时正文全宽，有图仅 112×96 缩略图；Weekly 以本周判断、领域走向、下一阶段观察为主，最后列 6 项证据。
- Weekly 保持周三/周日 UTC cadence，每次观察 `report_date - 6 days` 到当日；候选按发表时间，过滤信息稀疏项，综合输入最多 30 项，尽量覆盖六领域。旧报告保留原窗口与原综合文本。

### 可选 Tavily Search

本地 `.env` 或 GitHub → Settings → Secrets and variables → Actions 添加 `TAVILY_API_KEY`。
缺少 key 时日志为 `Tavily disabled: TAVILY_API_KEY missing`，其他源正常运行；无需新依赖。
`config/sources.yaml` 配置一个七领域 query pack，每次运行最多 7 次 basic 搜索，每次最多 5 项（总计 ≤35）。
使用 [Tavily 官方 Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search) 的 Bearer 认证与日期参数，关闭自动参数及 answer，无重试。

Fetcher 对 query pack 强制截取前 7 个查询，不依赖本地预算文件或 Actions Cache。
这是 per-run hard cap；手动重复运行会再次调用，不保证账户每日额度。没有 key 时不发请求。

原始网站域名作为文章来源（如 `nature.com`），Coverage 显示 Discovery Search。
raw 保留查询、领域提示、排名、分数、原域名和检索时间。通用搜索无发表日期时标记 `publication_date_known=false`：
Daily 显示“发现于”，Weekly 排除未知发表日期，不能把今天检索到的旧文当成本周趋势。

### 离线验证

Windows：`python -m pytest --basetemp=.pytest_tmp`。测试使用 mock/fixture，禁止外网与真实 LLM。
本地验证请使用数据库副本或测试数据库，渲染：
`python -m src.main render-ai4s --db <local-copy.db> --output-dir <preview-directory>`。
本次升级不要求清空数据库、修改 data 分支或全量重跑历史摘要。
