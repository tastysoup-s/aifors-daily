# AI Daily

每天自动抓取 AI / Agent 圈的重点更新，用大模型评分总结后生成一个静态 HTML 页面。Fork 一份、配一个 API key、打开 GitHub Actions，就有你自己的 AI 日报站。

## 这是什么

AI Daily 是一个自托管的每日 AI / Agent 资讯聚合工具。它每天定时从 RSS、arXiv、GitHub Topic、Hacker News 等多个来源抓取最新内容，用一个便宜的 LLM 给每条打分，再用一个更强的 LLM 把高分内容总结成中文摘要，最后渲染成一个单文件的静态 HTML 页面（含「今日新增」「归档」两栏），通过 GitHub Pages 对外展示。

整个项目走「fork 自部署」路线：每个用户 fork 这个仓库，在自己的 fork 里配置 Secrets、改 `config/preferences.yaml`，剩下的事情交给 GitHub Actions 的 daily cron。代码、配置和数据都在你自己的账号下，没有任何中心化服务器或共享数据库。

LLM 部分通过 [LiteLLM](https://github.com/BerriAI/litellm) 接入，所以你可以随时切换 Anthropic、OpenAI、DeepSeek、Gemini、Groq、Moonshot 等任意一家——只要在 Secrets 里配好对应 key，再把 `preferences.yaml` 里的模型字符串改掉就行，代码不用动。

## 本仓库部署说明

本 fork 位于 `tastysoup-s/aifors-daily`，保持上游 ai-daily 的原始 AI / Agent 聚合逻辑。评分和总结统一使用 `deepseek/deepseek-chat`，运行前只需配置 `DEEPSEEK_API_KEY`，不要把真实 key 写入代码、配置文件或提交历史。

- 本地：复制 `.env.example` 为 `.env`，只在 `.env` 中填写 `DEEPSEEK_API_KEY`；依次运行 `python -m src.main fetch`、`python -m src.main summarize`、`python -m src.main render --output-dir site`。
- GitHub Actions：在仓库 `Settings` → `Secrets and variables` → `Actions` 中添加名为 `DEEPSEEK_API_KEY` 的 Repository secret，然后从 Actions 页面手动运行 `daily`。工作流也会按原有 cron 自动运行。
- 数据分支：首次成功运行会创建 `data` 分支，并把 SQLite 数据库保存为 `data/ai_daily.db`、静态页面保存为 `docs/index.html`。
- GitHub Pages：在 `Settings` → `Pages` 中选择 `Deploy from a branch`，分支选 `data`，目录选 `/docs`。站点地址为 <https://tastysoup-s.github.io/aifors-daily/>。

`.env`、SQLite 数据库和本地渲染目录已被 `.gitignore` 排除；提交前仍应运行 `git status`，确认没有密钥或临时数据进入版本库。

## 快速开始（GitHub-only，无需本地 Python）

最推荐的上手路径完全不用动本地环境，全程在 GitHub 网页上操作。

1. **Fork 仓库**。点右上角 Fork，落到你自己账号下。

2. **挑一个 LLM 供应商，把它的 API key 加进你 fork 的 Secrets**。
   - 路径：`Settings` → `Secrets and variables` → `Actions` → `New repository secret`。
   - **你只需要其中一家的 key**——选 OpenAI、Anthropic、DeepSeek、Gemini、Groq、Moonshot 任意一家都行，看你手上已经有哪个 key。完整对照见下文「模型预设」和「完整支持的 provider 列表」。
   - Secret 名字必须是固定的：`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `GEMINI_API_KEY` / `GROQ_API_KEY` / `MOONSHOT_API_KEY`。其他几个没用到的留空即可（workflow 里都已经透传，没配的 Secret 自动是空字符串，不会报错）。
   - 这些 Secret 只存活在你自己 fork 的设置里，**对所有其他人（包括上游仓库）都不可见**。上游和其他 forker 用各自的 key，互不干扰。

3. **编辑 `config/preferences.yaml`，让 `models` 字段对得上你刚加的 key**（GitHub 网页点铅笔图标在线改即可）：
   - `models.scorer` / `models.summarizer`：换成你那家 provider 的模型字符串（格式 `<provider>/<model>`，见下文「模型预设」表）。本 fork 默认已配置为 DeepSeek；如果改用其他 provider，必须同步配置对应的 Secret。
   - `keywords`：换成你关心的方向。LLM 评分会把这些当主要风向标。
   - `score_threshold` / `top_n`：评分门槛和每日最多总结条数。默认 `7` / `10`，先按默认跑一两次再调。
   - 改完 Commit changes 到 main。

4. **启用 Actions**。Fork 出来的仓库 Actions 默认禁用，进 `Actions` Tab，点页面中央的 "I understand my workflows, go ahead and enable them"。

5. **手动跑一次 `daily` workflow**。`Actions` → 左边列表点 `daily` → 右上角 `Run workflow` → `Run workflow`。
   - 第一次跑大约 2-4 分钟。完成后查看 logs，最后几行应该有 `rendered=N output=docs`。
   - 同时仓库会多出一个 `data` 分支，里面有 `data/ai_daily.db` 和 `docs/index.html`。

6. **启用 GitHub Pages**。`Settings` → `Pages` → `Build and deployment` → `Source` 选 `Deploy from a branch` → `Branch` 选 `data`，folder 选 `/docs` → `Save`。一分钟后访问 `https://<你的用户名>.github.io/<仓库名>/` 就能看到日报。

7. **以后就不用管了**。Cron 每天 UTC 23:00（北京 07:00）自动跑一次，跑完就把当天新数据 commit 到 `data` 分支，Pages 自动更新。

## 模型预设

下面五套是 spec 推荐的组合，挑一个改到 `preferences.yaml` 的 `models` 字段里就行。月成本是按「每天跑 1 次、约 75 条候选打分、10 条总结」估算的，仅供参考，实际以各家计费为准。

| 预设 | scorer | summarizer | 需要的 Secret | 月成本（约） |
| --- | --- | --- | --- | --- |
| 省钱党（本 fork 默认） | `deepseek/deepseek-chat` | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` | < $1 |
| 平衡党 | `anthropic/claude-haiku-4-5` | `anthropic/claude-sonnet-4-6` | `ANTHROPIC_API_KEY` | $3 - $5 |
| 品质党 | `anthropic/claude-sonnet-4-6` | `anthropic/claude-opus-4-7` | `ANTHROPIC_API_KEY` | $20 - $40 |
| 中文混搭 | `deepseek/deepseek-chat` | `anthropic/claude-sonnet-4-6` | `DEEPSEEK_API_KEY` + `ANTHROPIC_API_KEY` | $2 - $4 |
| OpenAI 入门 | `openai/gpt-4o-mini` | `openai/gpt-4o` | `OPENAI_API_KEY` | ~ $1 |

LiteLLM 支持的所有 provider 都能用，模型字符串格式都是 `<provider>/<model>`，完整对照表见 [LiteLLM 文档](https://docs.litellm.ai/docs/providers)。

### 完整支持的 provider 列表

| Provider | 环境变量 | 模型示例 |
| --- | --- | --- |
| Anthropic | `ANTHROPIC_API_KEY` | `anthropic/claude-haiku-4-5`、`anthropic/claude-sonnet-4-6`、`anthropic/claude-opus-4-7` |
| OpenAI | `OPENAI_API_KEY` | `openai/gpt-4o-mini`、`openai/gpt-4o`、`openai/gpt-4.1`、`openai/gpt-5` |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek/deepseek-chat` |
| Gemini | `GEMINI_API_KEY` | `gemini/gemini-2.5-flash` 等 |
| Groq | `GROQ_API_KEY` | Llama 系列、Mixtral 等 |
| Moonshot | `MOONSHOT_API_KEY` | `moonshot/moonshot-v1-128k` 等 |

`scorer` 和 `summarizer` 不一定要用同一家。常见组合是「便宜模型评分 + 贵模型总结」，因为评分阶段要跑全量候选，token 用量是总结阶段的 5-10 倍。

### 怎么选模型

- **就想跑起来看看效果** → OpenAI 入门 或 省钱党。两套都能在一杯咖啡的钱里跑一整个月。
- **想要中文摘要质量好** → 中文混搭 或 品质党。Claude Sonnet/Opus 对中文输出风格更稳定。
- **关心性价比的稳态运行** → 平衡党。Haiku 评分够用，Sonnet 总结质量明显比 mini 系列高一档。
- **完全自由组合** → 任意 `<provider>/<model>` 字符串都能用。只要 LiteLLM 认得、对应 env var 也配了 key，就能跑。

## 在 fork 里改什么

绝大多数定制只需要改下面这几个文件，不用碰 `src/`。

- **`config/sources.yaml`** —— 信息源清单。每个条目都有 `name` 和 `type`，type 目前支持 `rss` / `arxiv` / `github` / `hackernews`。加新 RSS 就加一段 `type: rss` + `url`；想去掉某个源，注释掉或删掉那段就行。
- **`config/preferences.yaml`** —— 关键词、抓取窗口、模型、评分阈值、top_n。
- **`prompts/score.txt`** —— LLM 评分的 system+user prompt 模板。想让模型按你的偏好打分（例如更看重论文还是更看重产品发布），改这个文件最直接。
- **`prompts/summarize.txt`** —— LLM 总结的模板。换语言、换风格、换结构化字段都改这。

> 上游同步注意：未来会给上面这些「用户配置」文件加 `.gitattributes` 的 `merge=ours`，让你 `git pull upstream main` 的时候本地修改不会被覆盖。当前还没加，所以同步上游时记得自己处理一下冲突。

### `config/sources.yaml` 字段速查

| `type` | 必填字段 | 可选字段 | 说明 |
| --- | --- | --- | --- |
| `rss` | `name`, `url` | —— | 任意标准 RSS / Atom feed。 |
| `arxiv` | `name`, `categories` | `max_results`（默认 50） | `categories` 是 arXiv 分类码数组，如 `[cs.AI, cs.CL, cs.MA]`。 |
| `github` | `name`, `topic` | `min_stars`（默认 0） | 抓 GitHub Topic 下最新更新的仓库。 |
| `hackernews` | `name`, `query` | `min_points`（默认 100） | 走 HN Algolia 接口。`query` 只支持单 token / 空格分隔，**不支持** `OR` 和括号。 |

默认 `sources.yaml` 里有 9 个启用的源（3 个 GitHub Topic、5 个 RSS、1 个 HN）和 4 个被注释掉的占位（Anthropic / DeepSeek / LlamaIndex / 机器之心，对应站点目前没有可用 RSS）——如果哪天它们上线了 feed，把那几行取消注释填上 URL 即可。

### `config/preferences.yaml` 字段说明

```yaml
keywords: [LLM agent, MCP, tool use, RAG, multi-agent, reasoning models]
fetch_window_hours: 168           # 抓取窗口（小时），168 = 7 天
models:
  scorer:     anthropic/claude-haiku-4-5
  summarizer: anthropic/claude-sonnet-4-6
score_threshold: 7                # 评分 >= 该值才进入总结阶段
top_n: 10                         # 每天最多总结多少条
```

- `keywords` 改成你关心的方向，越具体效果越好（例如把 "LLM" 换成 "Mixture-of-Experts" 之类的窄词）。
- `fetch_window_hours` 默认 168 小时（7 天）。改小到 36 会经常漏掉 OpenAI/DeepMind 等低频博客；改大不会带来重复（有 URL 级 SQLite 去重）。
- `score_threshold` 提高会让总结更精，但每日可能不足 `top_n` 条。降低会让总结更多但杂。
- `top_n` 是个硬上限，控制每天 summarizer 阶段的 LLM 调用数（也就是成本）。

## 本地开发

如果你想本地调试 / 跑测试 / 改代码：

```bash
# 1. 创建并激活虚拟环境
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 2. 装依赖（含测试依赖）
pip install -r requirements-dev.txt

# 3. 准备环境变量
cp .env.example .env
# 用编辑器填入至少一个 LLM provider 的 key

# 4. 三个子命令依次跑一次
python -m src.main fetch
python -m src.main summarize
python -m src.main render
```

### CLI 一览

所有子命令共享 `--sources`、`--preferences`、`--db` 三个路径参数，默认值是 `config/sources.yaml` / `config/preferences.yaml` / `data/ai_daily.db`。

```bash
# 抓取所有 source，去重后写 SQLite
python -m src.main fetch
# 典型输出（INFO 日志）：
#   fetch summary: fetched=132 deduped=87 stored=87

# 给新条目打分（cheap LLM），≥ score_threshold 的 top_n 条进总结阶段（贵 LLM）
python -m src.main summarize
# 典型输出会包含分阶段成本：
#   scored=75 summarized=10 cost=$0.0264 (scorer=$0.0053 summarizer=$0.0211)

# 渲染静态站点
python -m src.main render --output-dir site --within-days 30
# 输出：
#   rendered=N output=site
```

`render` 的 `--output-dir` 默认 `site`，`--within-days` 默认 `30`（控制归档窗口）。GitHub Actions 里会传 `--output-dir docs` 以匹配 GitHub Pages。

### 跑测试

```bash
pytest
# 当前 82/82 通过
```

## 架构（简）

```
            ┌─────────────────────────────────────────────┐
            │             config/sources.yaml             │
            │           config/preferences.yaml           │
            └────────────────────┬────────────────────────┘
                                 │
                                 ▼
   ┌───────────┐  ┌────────┐  ┌────────┐  ┌────────────┐
   │   RSS     │  │ arXiv  │  │ GitHub │  │ HackerNews │   fetchers (异步，按源隔离失败)
   └─────┬─────┘  └────┬───┘  └────┬───┘  └─────┬──────┘
         └────────────┬┴───────────┴────────────┘
                      ▼
              ┌──────────────┐
              │  URL 去重     │
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │  items 表    │  ◄── SQLite (data/ai_daily.db)
              └──────┬───────┘
                     ▼
        ┌────────────────────────┐
        │   scorer LLM (便宜)     │
        │   ≥ threshold → top_n   │
        └──────────┬─────────────┘
                   ▼
        ┌────────────────────────┐
        │  summarizer LLM (贵)    │
        └──────────┬─────────────┘
                   ▼
              ┌──────────────┐
              │ summaries 表 │  ◄── 含 surfaced_at（今日 vs 归档）
              └──────┬───────┘
                     ▼
              ┌──────────────┐
              │   Jinja2     │
              │   render     │
              └──────┬───────┘
                     ▼
              site/index.html  →  GitHub Pages
```

SQLite 是唯一的持久层，两张主要表：`items`（抓取结果 + 评分）和 `summaries`（结构化摘要 + 首次出现时间 `surfaced_at`）。`surfaced_at` 这个字段是「今日新增 / 归档」分栏的依据：每次 `render` 把当天还未标记的 summary 标成今天「冒头」的，第二次同日 render 时今日栏就空了（这是有意的，去重语义）。

### Daily workflow 内部时序

GitHub Actions 里的 `daily` job 每次跑大致是这样的：

1. **Checkout `main`**：拿到代码、prompts、config（用户改过的就是用户改过的版本）。
2. **Checkout `data`** 到 `data-branch/`：拿到昨天遗留的 SQLite DB 和上次渲染的 HTML。首次运行时该分支不存在，这一步用 `continue-on-error: true` 静默失败。
3. **恢复状态**：把 `data-branch/data/` 和 `data-branch/docs/` 拷到工作目录的同名位置——这一步让 SQLite 累积历史成为可能。
4. **跑流水线**：`fetch` → `summarize` → `render --output-dir docs`。Render 的输出落在 `docs/`，刚好是 GitHub Pages 默认服务的目录名。
5. **回写 `data` 分支**：如果 `data-branch/` 是空的（首次运行），就 `git init -b data` 起一个 orphan 分支；否则就在已有分支上把 `data/` 和 `docs/` 拷新后 commit & push。Commit message 形如 `daily update 2026-05-18`。

整个流程不动 `main` 分支——`main` 只有你（人类）自己 push 代码改动时才会变。`data` 分支专门承载机器生成内容，所以你看 git log 的时候这两条历史线是清清楚楚分开的。

## 隐私 / 安全

- **Fork 默认是公开的**。这意味着 `config/preferences.yaml` 里你的关键词、模型选择，以及 `data` 分支里的 SQLite 数据库、HTML 页面，所有人都能看。
- 想把这些挡起来：把 fork 设成 private。注意 GitHub Pages 在 private 仓库上的免费策略历史上一直在变（早期只有 Pro 才行），看你 fork 时官方政策。
- **不要**把任何收件人邮箱、Webhook URL 之类的写进 `config/*.yaml`，那些会一起 commit 进 fork。需要敏感配置就用 Secrets（环境变量形式注入）。
- `.env` 已经在 `.gitignore` 里，本地放心填 key。

### 成本透明

`summarize` 每次运行都会在日志里打印总成本和按阶段拆分的成本，依据是 LiteLLM 的 `response._hidden_params["response_cost"]`：

```
scored=75 summarized=10 cost=$0.0264 (scorer=$0.0053 summarizer=$0.0211)
```

第一次手动 dry run 之后你会立刻看到真实数字，再决定是不是要换更便宜的预设。

## 常见问题

- **`Missing API key env vars: X`**
  你在 `preferences.yaml` 里指定的模型对应 provider 没配 key。要么去 Secrets 加 `X`，要么把 `models.scorer` / `models.summarizer` 改成你已经配了 key 的 provider。

- **arxiv 一直 429 / timeout**
  代码内置一次 retry（30s → 60s），如果还失败说明你这台 runner / IP 被 arxiv 限流了，等一小时再跑。极少数情况下需要把 `arxiv-cs-ai` 这个源临时注释掉。

- **日志里 `source X returned 0 items`**
  通常是该源 URL 已经失效。先去 `config/sources.yaml` 里手动访问那个 URL 看是不是 404 / 改版了，找到新地址替换；如果该站点彻底没了 RSS，注释掉。

- **「今日新增」段空了**
  这是有意的：同一天第二次 `render` 不会再把已经 surface 过的内容塞回今日栏。如果你确实想强制重新展示某条，手动去 SQLite 里把对应 `summaries.surfaced_at` 设为 NULL 再 render。

- **首次 Actions 跑失败，提示 `data` 分支不存在**
  Workflow 第一次会自己创建 orphan `data` 分支并 push。如果它在该阶段就挂了，确认 workflow 的 `permissions:` 块里有 `contents: write`，并且 fork 的 `Settings` → `Actions` → `General` → `Workflow permissions` 选了 `Read and write permissions`。

- **Pages 显示 404 / 显示了 README 而不是日报**
  Pages 的 `Source` 配置必须是 `Deploy from a branch` → branch `data` → folder `/docs`。如果选成了 `main` 或者 `/`（根目录），就会去渲染 README。

- **`summarize` 阶段总成本不对 / 没有显示成本**
  `response_cost` 由 LiteLLM 维护一张内部模型价格表。新模型可能暂时不在表里，会显示 `cost=0`。这种情况看 LiteLLM 版本是否需要升级（`requirements.txt` 里 `litellm>=1.50`，可手动 bump）。

- **GitHub API 限流（fetcher 报 403 / 429）**
  默认走匿名请求，公网 IP 每小时配额很小。在 Secrets 里加 `GITHUB_TOKEN`（其实 Actions 自动注入了 `secrets.GITHUB_TOKEN`，workflow 已经把它透传给程序），抓 GitHub Topic 时会自动用。本地开发要自己在 `.env` 里填一个 PAT。

- **想把模型换成本地 Ollama / vLLM**
  LiteLLM 支持，把 `models.scorer` 改成 `ollama/llama3:8b` 之类的字符串，再设 `OLLAMA_API_BASE` 环境变量。GitHub-hosted runner 跑不动本地模型，这种用法只适合自托管 runner。

## License

MIT. 详见 [LICENSE](./LICENSE)。

## Powered by

- [LiteLLM](https://github.com/BerriAI/litellm) —— 多 provider LLM 路由
- [Anthropic Claude](https://www.anthropic.com/) / [OpenAI](https://openai.com/) / [DeepSeek](https://www.deepseek.com/) —— 评分 + 总结
- [httpx](https://www.python-httpx.org/) —— 异步 HTTP 抓取
- [feedparser](https://feedparser.readthedocs.io/) —— RSS / Atom 解析
- [Jinja2](https://jinja.palletsprojects.com/) —— HTML 模板渲染
