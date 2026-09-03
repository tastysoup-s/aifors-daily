# AI4S-Daily 改造设计

> 状态：Phase 6 AI4S Summarizer 已完成，Fetch → Analyze → Summarize 核心后端链路已接通；日报、周报和前端尚未开始。

## 1. 项目目标

原 `ai-daily` 是面向通用 AI / Agent 的信息聚合、评分、摘要与静态页面系统。

`AI4S-Daily` 的目标是建设一个面向 AI for Science 的科研信息获取、筛选、分类、摘要、日报和周报系统。它关注 AI 是否真正参与科学问题的建模、模拟、预测、设计、实验或发现，而不是把所有带有 “AI” 或 “science” 的内容都视为 AI4S。

第一原则是可验证的科学价值：来源、分类、评分和摘要都要保留事实边界，优先呈现实验、数据、指标和可复现资源。

## 2. Baseline 数据流

当前已经验证的原版流程为：

```text
sources
→ fetcher
→ Item
→ dedup
→ SQLite
→ scorer
→ threshold
→ top_n
→ summarizer
→ Jinja2
→ Pages
```

`config/sources.yaml` 提供来源配置；四类 Fetcher 归一化为 `Item`；去重后写入 SQLite；Scorer 产生分数与标签；达到阈值的前 `top_n` 条目进入 Summarizer；最后由 Jinja2 生成静态页面。

这个 baseline 通过 `baseline-ai-daily` 标签保护。AI4S 改造必须分阶段进行，并保持每个阶段可回退、可测试。

## 3. AI4S 架构

当前已经接通的流程为：

```text
AI4S sources
→ Item
→ AnalyzerResult（Classification + AI4S Relevance + Scoring）
→ threshold / Top N
→ AI4SSummary
→ SQLite ai4s_analyses
```

后续目标流程为：

```text
AI4S sources
→ Item
→ AnalyzerResult（Classification + AI4S Relevance + Scoring）
→ threshold
→ AI4SSummary
→ Daily
→ Weekly
→ Frontend
```

Analyzer 在一次调用中回答“是否属于 AI4S、属于哪个领域、是什么内容类型、价值分数多高”，避免 Classification 与 Scorer 重复阅读同一输入。AI4SSummary 只对达到阈值的条目做事实约束下的结构化解释。

## 4. 领域分类体系

领域是第一套分类维度，第一版固定为 7 类：

| ID | 中文名 | 英文名 | 覆盖重点 |
| --- | --- | --- | --- |
| `biology` | 生物与生命科学 | Biology & Life Sciences | 蛋白质、组学、DNA/RNA、细胞、生物信息学与计算生物学 |
| `medicine` | 医学与药物 | Medicine & Drug Discovery | 药物发现、临床 AI、医学成像、生物医学 AI 与治疗 |
| `chemistry` | 化学 | Chemistry | 分子生成与性质、反应预测、逆合成、计算与量子化学 |
| `materials` | 材料科学 | Materials Science | 材料发现、晶体结构、性质预测、电池和催化材料 |
| `physics` | 物理科学 | Physics | 计算物理、粒子物理、天文、仿真、代理模型与物理科学机器学习 |
| `earth` | 地球与环境科学 | Earth & Environmental Sciences | 气候、天气、遥感、海洋、大气、环境科学 |
| `general` | 通用 AI4S | General AI for Science | 科学基础模型、科学智能体、自动化发现与通用科学推理 |

规范化名称和关键词以 `config/categories.yaml` 为准。第一版不增加更多一级领域；交叉学科问题未来可通过主领域、标签或多标签策略解决。

## 5. 内容类型

内容类型是独立于领域的第二套维度：

| ID | 展示名称 | 含义 |
| --- | --- | --- |
| `paper` | Paper | 论文、预印本或明确的研究工作 |
| `model` | Model | 可使用或公开说明的模型、权重与模型版本 |
| `dataset` | Dataset | 科研数据集、数据库或数据资源 |
| `benchmark` | Benchmark | 评测任务、基准数据和评价框架 |
| `tool` | Tool | 面向科研的库、软件、服务或实验工具 |
| `project` | Project | 综合研究项目、平台或长期计划 |
| `research_news` | Research News | 实验室、机构或媒体发布的研究新闻 |

例如，一项蛋白质结构模型的领域可以是 `biology`，内容类型可以是 `model`；前者回答科学领域，后者回答内容载体，两者不能合并为一个枚举。

## AI4S Data Model

Phase 3 采用新增兼容模型，不直接替换原版 `Score`、`Summary` 和 `Analysis`。这些旧模型仍服务当前 SQLite、Summarizer 和 Jinja2 Pipeline；新模型在 Phase 4/5 完成存储映射和调用接入。

### Item

`Item` 继续只表示 Fetcher 标准化后的原始内容：URL、标题、正文、发布日期、来源和原始载荷。领域、评分和摘要不放进 `Item`，因为它们是可重跑、与模型版本相关的派生结果；同一 Item 未来可能拥有不同 Analyzer 版本。

### AnalyzerResult

`AnalyzerResult` 合并 Classification、AI4S relevance 和最终排序分数：

| 字段 | 类型 | 约束/语义 |
| --- | --- | --- |
| `is_ai4s` | `bool` | 是否真正属于 AI for Science |
| `primary_category` | `AI4SCategory \| None` | AI4S 内容必须是 7 类之一；非 AI4S 必须为 `None` |
| `secondary_categories` | `list[AI4SCategory]` | 最多 2 个、无重复、合法且不含主领域；非 AI4S 必须为空 |
| `content_type` | `AI4SContentType` | `paper/model/dataset/benchmark/tool/project/research_news` |
| `score` | `int` | 0–10，且拒绝 `bool` |
| `tags` | `list[str]` | 方法或主题标签，不机械复制一级领域 |
| `model` | `str` | Analyzer 使用的模型标识 |
| `cost_usd` | `float` | Analyzer 调用成本 |

`general` 是合法的跨学科 AI4S 主领域，不能同时表示“不是 AI4S”；因此 `is_ai4s=False` 时使用 `primary_category=None`。Python 目前用标准库 `Literal` 提供静态类型提示，并用常量集合执行运行时校验。`categories.yaml` 仍是 taxonomy 配置源，契约测试保证当前 Python ID 与 YAML 一致；Phase 4/5 应由配置加载后的 allowed categories 驱动运行时校验，避免长期维护两套定义。

### AI4SSummary

`AI4SSummary` 使用科研语义字段：`scientific_problem`、`ai_method`、`main_result`、`innovation`、`scientific_significance`、`resources`、`model`、`cost_usd`。它不永久携带旧 Summary 中语义重叠的 `approach/metrics/links/why_relevant`；旧模型暂时保留只是为了兼容现有 Pipeline。

### AI4SAnalysis

`AI4SAnalysis` 是未来展示与 Report 层消费的聚合对象：`Item + AnalyzerResult + optional AI4SSummary + surfaced_at`。Summary 允许为 `None`，表示已经分析但未达到摘要阈值或尚未摘要。网页和 Report 最终只消费该对象，不应理解数据库 Row 或原始 LLM JSON。

```text
Fetched
  ↓
Item
  ↓
Analyzed → AnalyzerResult
  ├─ is_ai4s = false 或 score < threshold → 停止深度摘要
  └─ is_ai4s = true 且 score >= threshold → AI4SSummary
                                                ↓
                AI4SAnalysis（Summary 可选） ←──┘
```

### Future Database Mapping and Reports

Phase 4 才设计 `AnalyzerResult` 与 `AI4SSummary` 的 SQLite 表/字段、migration、版本和历史兼容；本阶段没有修改 schema 或 `storage.py`。Report 接口未来至少需要 `ReportType`、`period_start`、`period_end`、`generated_at` 和选中的 `AI4SAnalysis` 列表，但 Phase 8/9 前不新增 Report 类或生成器。

## 6. Source Strategy

### 第一版

主要依赖现有 arXiv、RSS、GitHub 和 Hacker News Fetcher，以低开发成本形成跨学科且数量受控的候选池：

- arXiv 拆为 AI 方法、生物医学、化学材料、物理地球四组，总上限 50。
- GitHub 只启用已核验的四个 AI4S Topic，并通过 Star 门槛控制噪音。
- RSS 只保留现有科研机构和模型平台，不新增大批站点。
- Hacker News 只作为高热度补充，不是核心科研来源。

重要限制：订阅 `q-bio.*`、`physics.*` 或 `cond-mat.*` 只能形成 Science Domain Candidate，其中会包含大量未使用 AI 的纯科学论文。未来必须执行：

```text
Science Domain Candidate
+ AI Relevance Classification / Scoring
→ AI4S Candidate
```

配置取舍、Topic 实测和成本估计见 `docs/ai4s-sources.md`。

### 第二版

在第一版运行数据可用于评估后，再独立设计 bioRxiv、PubMed、OpenAlex 等 Fetcher。新增来源必须同时评估授权、接口稳定性、去重键、时间字段、内容完整度和每日调用成本，不能只追求召回率。

## 7. Analyzer Pipeline

Analyzer 位于 `src/ai4s_analyzer.py`，通过一次 `complete_json()` 调用合并 Classification、AI4S relevance 和 Scoring，并复用 `models.scorer`。输入保持最小且可审计：

```text
AI4S taxonomy
用户关注关键词
source
published date
title
content
```

结构化输出使用 `AnalyzerResult`：

```text
is_ai4s
primary_category
secondary_categories
content_type
score
tags
model
cost_usd
```

- `primary_category` 和 `secondary_categories` 使用第 4 节固定的 7 个 ID。
- `content_type` 使用第 5 节定义的内容类型。
- `score` 是 AI4S 相关性、科学价值、技术创新、证据质量和科研影响的最终排序分数。

正式 Prompt 位于 `prompts/analyze_ai4s.txt`，正文截断为 1200 字符。命令 `python -m src.main analyze --db data/ai4s_dev.db [--limit N]` 只读取未分析 Item；成功和非 AI4S 结果都会持久化，失败项保留为未分析以便下次重试。第一版不保存未经校准、容易造成误解的自报 `confidence`。

## 8. AI4S Scoring

未来 Scorer 至少综合以下维度：

1. AI4S relevance：AI 是否直接服务于明确科学问题。
2. Scientific significance：科学问题和发现的意义。
3. Technical novelty：方法、建模或系统层面的创新。
4. Evidence / experimental quality：数据、基线、定量结果和可复现证据。
5. Practical scientific impact：对实验、模拟、预测、设计或科研效率的实际影响。

第一版接口仍输出 `score` 和 `tags`，以便未来兼容现有 Pipeline。草稿位于 `prompts/score_ai4s_draft.txt`，本阶段不替换现有 `prompts/score.txt`。

## 9. AI4S Summarization

AI4S Summarizer 位于 `src/ai4s_summarizer.py`，使用 `models.summarizer` 和正式 Prompt `prompts/summarize_ai4s.txt`。它只处理 `is_ai4s=true`、达到 `score_threshold`、且 `summarized_at IS NULL` 的分析结果，并按分数、发布时间排序。`top_n` 控制单批默认成本，CLI 的 `--limit` 可进一步收紧调用数量：

```text
python -m src.main summarize-ai4s --db data/ai4s_dev.db [--limit N]
```

摘要围绕六个字段组织：

```text
scientific_problem
ai_method
main_result
innovation
scientific_significance
resources
```

摘要必须区分科学问题与 AI 方法、创新与科学意义，优先提供定量结果，不得补写输入未披露的数据或资源；缺少信息时明确写“原文未说明”或“未披露”。输出经过必要字段、字符串类型和推断性措辞校验，单条失败不会阻断批次，也不会写入 `summarized_at`。原版 `src/summarizer.py` 和 `prompts/summarize.txt` 保持不变。

## 10. Daily Report

未来 Daily Report 每日生成一次，按以下层次组织：

```text
Highlights
+ Category
```

Highlights 展示跨领域最重要的少量工作；Category 分区覆盖 7 个领域，并允许某日无内容的领域保持为空。具体选择规则、配额和模板在后续阶段实现，本阶段不修改 Jinja2。

## 11. Weekly Report

未来 Weekly Report 每周生成两次。它不是日报的机械拼接，而是：

```text
聚合
→ 趋势分析
→ 代表工作
→ 研究热点
```

周报需要消除跨日重复，识别连续主题，比较不同方法和证据，并说明热点是短期新闻密度还是持续研究趋势。生成逻辑、时间窗口和成本预算应独立设计。

## 12. Frontend

未来前端提供：

```text
Daily / Weekly switch
+ Category navigation
```

仍优先保持静态、轻量和可部署。是否继续使用现有 Jinja2 模板应在数据模型稳定后评估；本阶段不改布局、不引入 React/Vue，也不修改任何模板。

## 13. Database

AI4S 派生状态保存在独立的 `ai4s_analyses` 表中，以 `items.url` 为外键和主键。Analyzer 的分类、类型、分数、标签、模型、成本与时间保存在该表；AI4SSummary 的六个字段、模型、成本和 `summarized_at` 更新在同一行中。`summarized_at` 是摘要完成状态的依据。

`Storage.init()` 使用 `CREATE TABLE IF NOT EXISTS` 同时支持全新数据库和只有 `items`、`summaries` 的旧数据库。旧 Pipeline 继续使用 `summaries`，两套状态互不覆盖。

## Development Environment / State Isolation

- `main` 保持原版稳定 baseline；AI4S 开发只在 `feat/ai4s-redesign` 进行。
- `data/ai_daily.db` 是原版与历史运行的 reference DB，后续 AI4S 实验不得使用、清空、迁移或覆盖它。
- `data/ai4s_dev.db` 是 AI4S 开发数据库，不复制历史数据。
- AI4S 核心命令为 `fetch`、`analyze` 和 `summarize-ai4s`，三者都通过 `--db data/ai4s_dev.db` 显式使用开发库；默认路径仍保持 `data/ai_daily.db`。
- AI4S 开发阶段不得用旧版 Prompt 对新 AI4S Item 运行原版 `summarize`；涉及 schema migration 前必须先备份对应数据库。

## 14. Development Roadmap

| Phase | 目标 | 当前状态 |
| --- | --- | --- |
| Phase 0 Baseline | 固化并验证原 ai-daily Pipeline | 完成 |
| Phase 1 Domain | 固化 7 类领域、内容类型和总体设计 | 本阶段完成 |
| Phase 2 Sources | 运行第一版 Source Pool，以真实数据校准数量与质量 | 完成首轮配置与验证 |
| Phase 3 Model | 设计 AI4S 领域对象与边界 | 完成 |
| Phase 4 DB | 设计并实现可迁移的 AI4S schema | 完成 |
| Phase 5 Analyzer | 合并领域、内容类型、AI relevance 和 Scoring | 完成 |
| Phase 6 Summary | 接入 AI4S 结构化摘要 | 完成 |
| Phase 7 Daily | 实现每日 Highlights 与分类报告 | 下一阶段 |
| Phase 8 Weekly | 实现每周两次的聚合和趋势分析 | 待开始 |
| Phase 9 Frontend | 增加 Daily/Weekly 切换与分类导航 | 待开始 |
| Phase 10 Workflow | 调整运行计划、发布和故障处理 | 待开始 |
| Phase 11 Tests/Docs | 完成端到端测试和运维文档 | 待开始 |

Phase 6 完成后，核心 AI4S backend processing 已形成。下一阶段只应设计 **Daily Report**，Weekly、Frontend 和 Workflow 尚未开始。
