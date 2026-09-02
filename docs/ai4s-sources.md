# AI4S 信息源盘点与第一版策略

## 范围与结论

本文件记录 2026-09-03 对原 `config/sources.yaml`、现有 Fetcher 和候选 GitHub Topics 的盘点。第一版只复用 `arxiv`、`rss`、`github`、`hackernews`，不新增 Fetcher。

核心约束：arXiv Fetcher 会把同一 source 的多个 category 用 `OR` 连接；GitHub Fetcher 按“近 7 天有 push + 最低 Star”检索，每个 topic 最多返回 30 个仓库；RSS 没有关键词过滤。配置层因此只能控制候选量，不能替代未来的 AI 相关性判断。

## 原 Source 逐项盘点

| Source | 当前用途 | 是否保留 | AI4S 相关性 | 后续处理 |
| --- | --- | --- | --- | --- |
| `arxiv-cs-ai` | 通用 AI、语言与多智能体论文 | 改造 | 中 | 拆成 4 个限额更小的 AI4S 领域池 |
| `huggingface-blog` | 模型、数据集和开源生态 | 候选 | 中 | 本机重复连接失败，第一版不启用；恢复前先验证 feed 可达性 |
| `github-trending-agent` | Agent 项目 | 不保留 | 低 | 从活动配置移除；通用 Agent 不再作为 AI4S 核心 |
| `github-trending-llm` | LLM 项目 | 不保留 | 低 | 从活动配置移除；由 AI4S 专题 Topic 替代 |
| `github-trending-mcp` | MCP 项目 | 不保留 | 低 | 从活动配置移除 |
| `openai-blog` | 通用模型与产品新闻 | 候选 | 低至中 | 第一版不启用；有稳定 AI4S 专题后再评估 |
| `deepmind-blog` | 实验室研究新闻 | 保留 | 高 | 作为跨领域 AI4S Research News 核心补充 |
| `google-research` | Google Research 研究新闻 | 候选 | 中至高 | 本机重复连接失败，第一版不启用；网络条件改善后恢复 |
| `nvidia-dev-blog` | GPU、工程与科研计算内容 | 保留 | 中 | 保留但依赖后续 Scorer 过滤通用工程文章 |
| `microsoft-research` | 跨学科研究新闻 | 保留 | 中至高 | 作为研究新闻来源 |
| `together-ai-blog` | 模型与推理平台内容 | 不保留 | 低 | 第一版移出活动配置 |
| `langchain-blog` | Agent 框架内容 | 不保留 | 低 | 第一版移出活动配置 |
| `lilian-weng` | AI 技术长文 | 候选 | 低至中 | 暂不启用；仅在明确覆盖科学智能时恢复 |
| `simon-willison` | LLM 工具与行业动态 | 不保留 | 低 | 第一版移出活动配置 |
| `sebastian-raschka` | 机器学习技术解读 | 候选 | 中 | 暂不启用，避免通用 ML 内容占用 Scorer |
| `andrej-karpathy` | 通用深度学习与教育内容 | 候选 | 低至中 | 暂不启用 |
| `import-ai` | AI 通讯 | 候选 | 中 | 暂不启用；原 feed 连接稳定性也需后续复核 |
| `hackernews-ai` | 高热度通用 AI 社区新闻 | 改造 | 低 | 查询改为 `AI science`，门槛保持 100 分，仅作补充 |
| `qbitai` | 中文通用 AI 媒体 | 候选 | 低至中 | 第一版不启用，后续单独评估 AI4S 信号密度 |

## arXiv 第一版配置

所用代码已在 [arXiv 官方分类表](https://export.arxiv.org/category_taxonomy) 中核验。总 `max_results` 为 50，与 baseline 的单一 arXiv source 上限相同。

| Source | Categories | 上限 | 覆盖意图 | 主要风险 |
| --- | --- | ---: | --- | --- |
| `arxiv-ai-methods` | `cs.AI`, `cs.LG`, `stat.ML` | 20 | 通用 AI4S 方法、科学机器学习基础 | 通用 AI 论文仍可能偏多 |
| `arxiv-biology-medicine` | `q-bio.BM`, `q-bio.GN`, `q-bio.QM` | 10 | 生物分子、基因组、定量生物方法 | 会混入未使用 AI 的纯生物论文 |
| `arxiv-chemistry-materials` | `physics.chem-ph`, `cond-mat.mtrl-sci` | 10 | 计算化学与材料科学 | 会混入传统计算/实验研究 |
| `arxiv-physics-earth` | `physics.comp-ph`, `astro-ph.IM`, `physics.ao-ph`, `eess.IV` | 10 | 计算物理、天文方法、大气海洋、遥感/成像 | `eess.IV` 也包含非地球与医学成像内容 |

当前 Fetcher 不能表达“科学领域 AND AI 关键词”。因此这些条目只是 **Science Domain Candidate**，未来必须再经过 **AI Relevance Classification / Scoring**，不能仅凭 arXiv 分类直接进入日报。

## GitHub Topic 实测

使用 GitHub Search API 检查 Topic 是否存在，并用当前 Fetcher 的“近 7 天有 push”语义估算活跃候选量。数量是 2026-09-03 快照，会随时间变化。

| Topic | 全量仓库数 | 近 7 天条件 | 活跃候选 | 状态 | 判断 |
| --- | ---: | --- | ---: | --- | --- |
| `ai-for-science` | 280 | stars ≥ 20 | 16 | 保留 | 跨领域 AI4S 信号明确，仍需排除聚合列表和通用科研 Agent |
| `scientific-machine-learning` | 534 | stars ≥ 50 | 40 | 候选 | 质量较高但单 Topic 量已超过 Fetcher 30 条上限，且成熟仓库频繁 push |
| `drug-discovery` | 1853 | stars ≥ 100 | 20 | 保留 | 医药和分子建模覆盖好，少量通用科学 Agent 噪音可由后续评分处理 |
| `protein-design` | 296 | stars ≥ 20 | 5 | 保留 | 数量小、主题集中 |
| `materials-informatics` | 305 | stars ≥ 20 | 8 | 保留 | 数量小、材料相关性高 |
| `computational-biology` | 2359 | stars ≥ 100 | 19 | 不采用 | 头部结果混入课程、通用工具与仅弱相关仓库，Topic 过宽 |

第一版活动配置只启用 `ai-for-science`、`drug-discovery`、`protein-design`、`materials-informatics`。不保留原来的 `agent`、`llm`、`mcp` Topic。

## RSS 与 Hacker News

RSS 不扩展新站点，活动配置只保留本机实测成功的 DeepMind、NVIDIA Developer Blog 和 Microsoft Research。它们可能发布通用 AI 或工程内容，所以定位为 Research News / Model / Tool 补充，不能绕过 AI4S Scorer。Hugging Face 与 Google Research 仍记录为候选，但因本机重复连接失败暂不启用。

Hacker News 使用 `AI science` 且 `min_points: 100`。实测近 7 天只有约 1 个结果达到门槛，适合作为低流量社区补充；较低门槛会快速出现公司知识库、娱乐项目等噪音。

## 候选量与成本风险

- arXiv 硬上限：50 条/次。
- 当前 GitHub 活跃快照：约 49 条；每个 Topic 的 Fetcher 硬上限仍为 30。
- HN：当前约 1 条/7 天。
- RSS：没有配置级条数上限，实际取决于三个活动 feed 在 7 天窗口内的发布量。

冷启动或首次切换 Source Pool 时，合理估计约产生 **100-150 条候选 Item**；稳定每日运行并经 URL 去重后，新增量通常应低于该值，粗略预计 **20-60 条/日**。最大不确定性来自 RSS 发布量和跨领域 arXiv 的非 AI 论文。该规模低于 baseline 实测的 231 条冷启动 Item，但在 Classification 上线前仍应监控每次 `stored` 数量，避免直接扩大 category 或降低 GitHub Star 门槛。

### 本机 Fetch 验证

2026-09-03 首轮配置实测抓取 117 条、批内/历史去重后新增 80 条；arXiv、四个 GitHub Topic、DeepMind、NVIDIA、Microsoft Research 和 HN 均正常返回。Hugging Face 与 Google Research 出现 `ConnectError`，因此已从第一版活动配置降为候选。收敛为 12 个活动 Source 后再次运行，全部 Source 成功，结果为 `fetched=117 deduped=0 stored=0`；第二次未新增是因为同一批 URL 已由首轮写入，说明历史去重正常。整个验证过程没有运行 Summarizer，也没有产生新的 LLM 调用成本。

## 后续独立任务

1. 设计 AI4S data model 和 Classification schema。
2. 实现 `category + content_type + confidence` 分类，并增加 AI relevance gate。
3. 基于实际一至两周数据校准各 source 限额和阈值。
4. 再独立评估 bioRxiv、PubMed、OpenAlex 等新 Fetcher；本阶段不实现。
