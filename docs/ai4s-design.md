# AI4S-Daily 改造设计

> 状态：Phase 1 设计基线。本文固化未来方向，但本阶段不接入新数据模型、不修改数据库、不实现分类器，也不改变日报、周报或前端。

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

## 3. AI4S 目标架构

未来目标流程为：

```text
AI4S sources
→ Item
→ Classification
→ AI4S Scorer
→ AI4S Summary
→ Daily
→ Weekly
→ Frontend
```

Classification 与 Scorer 承担不同职责：Classification 回答“属于哪个领域、是什么内容类型、置信度多高”；Scorer 回答“这项工作对 AI4S 读者有多大价值”。Summary 只对入选条目做事实约束下的结构化解释。

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

| 类型 | 含义 |
| --- | --- |
| `Paper` | 论文、预印本或明确的研究工作 |
| `Model` | 可使用或公开说明的模型、权重与模型版本 |
| `Dataset` | 科研数据集、数据库或数据资源 |
| `Benchmark` | 评测任务、基准数据和评价框架 |
| `Tool` | 面向科研的库、软件、服务或实验工具 |
| `Project` | 综合研究项目、平台或长期计划 |
| `Research News` | 实验室、机构或媒体发布的研究新闻 |

例如，一项蛋白质结构模型的领域可以是 `biology`，内容类型可以是 `Model`；两者不能合并为一个枚举。本阶段只定义体系，不修改 `src/models.py`。

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

## 7. Classification 未来设计

Classification 是后续独立阶段，本次不实现。建议输入保持最小且可审计：

```text
title
content
source
```

未来结构化输出：

```text
category
content_type
confidence
```

- `category` 使用第 4 节固定的 7 个 ID。
- `content_type` 使用第 5 节定义的内容类型。
- `confidence` 表示分类可信度，不等同于内容价值分数。

分类器还应显式判断 AI relevance，避免把纯科学论文或普通 AI 新闻误送入 AI4S Scorer。具体字段约束、单标签或多标签策略、失败回退与数据库 migration 必须在下一阶段先设计再实现。

## 8. AI4S Scoring

未来 Scorer 至少综合以下维度：

1. AI4S relevance：AI 是否直接服务于明确科学问题。
2. Scientific significance：科学问题和发现的意义。
3. Technical novelty：方法、建模或系统层面的创新。
4. Evidence / experimental quality：数据、基线、定量结果和可复现证据。
5. Practical scientific impact：对实验、模拟、预测、设计或科研效率的实际影响。

第一版接口仍输出 `score` 和 `tags`，以便未来兼容现有 Pipeline。草稿位于 `prompts/score_ai4s_draft.txt`，本阶段不替换现有 `prompts/score.txt`。

## 9. AI4S Summarization

未来摘要应围绕六个字段组织：

```text
scientific_problem
ai_method
main_result
innovation
scientific_significance
resources
```

摘要必须区分科学问题与 AI 方法，优先提供定量结果，不得补写输入未披露的数据；缺少指标时明确写“未披露”。语言应通俗、准确、克制，避免营销腔。草稿位于 `prompts/summarize_ai4s_draft.txt`，本阶段不替换现有 Prompt 或修改 `src/summarizer.py`。

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

## 13. Database Future Work

本阶段没有修改 SQLite schema、`src/storage.py` 或 `src/models.py`。

后续需要单独设计 migration，至少考虑分类结果、内容类型、置信度、AI relevance、日报/周报归属、Prompt/模型版本和历史重跑。设计时必须明确旧数据库升级、幂等写入、回滚和 baseline 数据兼容策略。

## 14. Development Roadmap

| Phase | 目标 | 当前状态 |
| --- | --- | --- |
| Phase 0 Baseline | 固化并验证原 ai-daily Pipeline | 完成 |
| Phase 1 Domain | 固化 7 类领域、内容类型和总体设计 | 本阶段完成 |
| Phase 2 Sources | 运行第一版 Source Pool，以真实数据校准数量与质量 | 配置准备完成，待观察 |
| Phase 3 Model | 设计 AI4S 领域对象与边界 | 待开始 |
| Phase 4 DB | 设计并实现可迁移的 schema | 待开始 |
| Phase 5 Classification | 实现领域、内容类型、置信度和 AI relevance 分类 | 待开始 |
| Phase 6 Scorer | 接入并校准 AI4S Scorer | 待开始 |
| Phase 7 Summarizer | 接入 AI4S 结构化摘要 | 待开始 |
| Phase 8 Daily | 实现每日 Highlights 与分类报告 | 待开始 |
| Phase 9 Weekly | 实现每周两次的聚合和趋势分析 | 待开始 |
| Phase 10 Frontend | 增加 Daily/Weekly 切换与分类导航 | 待开始 |
| Phase 11 Workflow | 调整运行计划、发布和故障处理 | 待开始 |
| Phase 12 Tests/Docs | 完成端到端测试和运维文档 | 待开始 |

下一阶段只应进行 **AI4S data model + Classification schema design**，不应跳过模型和 migration 设计直接实现分类器。
