# Benchmark evidence

Snapshot: 2026-10-10. 9 models, 948 observations: 886 independent and 62 vendor-hosted/self-reported.

[Per-model effort curves and resource proxies](effort-curves.md)

Scope: latest retained Claude 5, GPT-6, Gemini 4 Argon, and Grok 4.7 classes only. Gemini/Grok runtime integration is planned. Earlier generations, superseded revisions and other vendors are excluded.

Evidence only: model routing and classifier evaluation are not implemented by this dataset. `active` is proposed eligibility, not a deployed routing claim. [Source methods and gaps](../../data/benchmarks/SOURCES.md) explain comparability and scope.

1. Validate: `python3 scripts/benchmarks.py validate --as-of 2026-10-10`
2. Re-render offline: `python3 scripts/benchmarks.py render --as-of 2026-10-10`
3. Test: `python3 -m unittest discover -s tests -p test_benchmarks.py -v`

Refresh requires re-reading primary sources and updating JSON/provenance first. Rendering never fetches new scores. Staleness is >60 days since publication/evaluation or observed snapshot; a fresh observation does not establish a fresh evaluation execution date.

Bars preserve effort and cohort. Solid bars are independent, hatched bars vendor-hosted. Black error lines show score intervals; confidence level is unknown unless provided. No score is copied across models or efforts. Lower-is-better metrics retain their raw bar direction.

Arena Text/WebDev/Vision measure human preference; planned design caps relevant preference at 15%. Search has no retained rows. LongerQuery is report-only and does not prove long-context retrieval. Agent Code net improvement is a separate observational causal-effect statistic, report-only, with 95% CIs and session counts instead of votes.

Deep web research evidence uses the v1.1 `web_research` trait. DRACO report rubrics, WANDR frozen-index supported-fact soft F1, Mercor BrowseComp subsets and Epoch closed-book factuality retain distinct protocols. None establishes 10–15-minute lead-dossier success. Mercor ±error is unverified uncertainty; its n_samples counts are not successes. Missing product-to-base-model identity remains a gap.

## Coverage

| Model | Runtime | Filled benchmarks | Independent rows | Self-reported rows |
|---|---|---:|---:|---:|
| claude-fable-5-1 | claude | 41/83 | 119 | 15 |
| claude-opus-5-5 | claude | 43/83 | 112 | 15 |
| claude-sonnet-5-5 | claude | 45/83 | 113 | 18 |
| claude-haiku-5-5 | claude | 28/83 | 99 | 5 |
| gpt-6-astra | codex | 40/83 | 117 | 3 |
| gpt-6.1-sol | codex | 36/83 | 108 | 0 |
| gpt-6-luna | codex | 38/83 | 122 | 3 |
| gemini-4-argon | planned | 29/83 | 26 | 3 |
| grok-4.7 | planned | 34/83 | 70 | 0 |

[Complete model × benchmark values, with efforts and source kinds](coverage.csv)

## Heatmap

One model row per exact effort; no best-effort average. Missing cells are gray. Multiple observations have `*`; open the benchmark chart or JSON for all rows.

![Model × benchmark observations](charts/heatmap.svg)

## Benchmark charts

### Terminal-Bench 4.0 / AA

66 terminal tasks, pass@1 over three repeats using mini-swe-agent. Not saturated in the observed snapshot. (40 observations; proposed eligible; task_completion).

![Terminal-Bench 4.0 / AA](charts/terminal-bench-4.0-aa.svg)

### AutomationBench-AA

Share of SaaS task objectives completed; a guardrail violation zeros that task. Not the full-task-completion metric. Not saturated in the observed snapshot. (40 observations; proposed eligible; task_completion).

![AutomationBench-AA](charts/automationbench-aa.svg)

### HLE / AA text-only, no tools

2,158 text-only questions from the May 2025 HLE revision, pass@1; GPT-5.6 Luna medium equality checker. Difficult reasoning is a review proxy. Not saturated in the observed snapshot. (40 observations; proposed eligible; task_completion).

![HLE / AA text-only, no tools](charts/hle-aa-no-tools.svg)

### AA-LCR v1.1

100 long-document retrieval and reasoning questions (~100K input), three repeats, equality-checker pass@1. Not saturated in the observed snapshot. (40 observations; proposed eligible; task_completion).

![AA-LCR v1.1](charts/aa-long-context-v1.1.svg)

### AA-Omniscience accuracy

Factual accuracy on 6,000 private questions; distinct from refusal and hallucination metrics. Not saturated in the observed snapshot. (40 observations; proposed eligible; task_completion).

![AA-Omniscience accuracy](charts/aa-omniscience-accuracy.svg)

### AA-Omniscience hallucination rate

Hallucination frequency as defined by AA; lower is better. Refusal behavior affects interpretation. Not saturated in the observed snapshot. (40 observations; proposed eligible; task_completion).

![AA-Omniscience hallucination rate](charts/aa-omniscience-hallucination-rate.svg)

### SciCode / AA v1.0.1

288 Python scientific subproblems with scientist background prompting, pass@1 over three repeats; 300-second grading timeout. AA flags SciCode under review; inactive until reliability review resolves. (40 observations; report-only; task_completion).

![SciCode / AA v1.0.1](charts/scicode-aa-v1.0.1.svg)

### GDP.pdf / AA all-pass

Professional document reasoning, 100 tasks, five attempts; share of attempts passing every criterion. Not saturated in the observed snapshot. (40 observations; proposed eligible; task_completion).

![GDP.pdf / AA all-pass](charts/gdp-pdf-aa.svg)

### Terminal-Bench-Science 0.1 / AA

70 expert research workflows in a terminal; all tests must pass, pass@1 over three repeats. Not saturated in the observed snapshot. (19 observations; proposed eligible; task_completion).

![Terminal-Bench-Science 0.1 / AA](charts/terminal-bench-science-0.1-aa.svg)

### IFBench / AA

294 constrained-output questions, rule-based assessment, five repeats. Not saturated in the observed snapshot. (0 observations; proposed eligible; task_completion).

![IFBench / AA](charts/ifbench-aa.svg)

### GPQA Diamond / AA

198 difficult science multiple-choice questions, pass@1. Removed from AA Intelligence Index in v4.2; frontier saturation reduces discrimination; inactive. (10 observations; report-only; task_completion).

![GPQA Diamond / AA](charts/gpqa-diamond-aa.svg)

### Terminal-Bench 2.1 / AA

89 terminal tasks, Terminus 2 on E2B, pass@1 averaged over three repeats. Superseded by 4.0 for current routing; retain independently versioned observations only. (10 observations; report-only; task_completion).

![Terminal-Bench 2.1 / AA](charts/terminal-bench-2.1-aa.svg)

### tau²-bench Telecom / AA

Dual-control telecom support tool use; preserve the domain and user simulator protocol. Legacy AA suite; replaced by tau³-Banking; no current scoped scores verified. (0 observations; report-only; task_completion).

![tau²-bench Telecom / AA](charts/tau2-telecom-aa.svg)

### LiveCodeBench / AA legacy

AA legacy competitive-code generation evaluation; sample date window is unverified in this snapshot. AA retired active reporting; exact historical sample window unknown. No routing use. (0 observations; report-only; task_completion).

![LiveCodeBench / AA legacy](charts/livecodebench-aa-legacy.svg)

### AA-AnalystAgent pass^5

80 data-analysis questions, five attempts; headline requires success in all five. Not saturated in the observed snapshot. (5 observations; proposed eligible; task_completion).

![AA-AnalystAgent pass^5](charts/aa-analyst-agent-pass5.svg)

### AA Intelligence Index v4.3.2

Composite of ten AA evaluations; do not double count against its constituents. Composite retained for reference only; routing should use task-specific component results. (40 observations; report-only; composite).

![AA Intelligence Index v4.3.2](charts/aa-intelligence-v4.3.2.svg)

### AA-Briefcase v1.1 Elo

Combined rubric, analytical quality and presentation Elo for 91 long-horizon professional tasks. Not saturated in the observed snapshot. (40 observations; proposed eligible; task_completion).

![AA-Briefcase v1.1 Elo](charts/aa-briefcase-v1.1.svg)

### GDPval-AA v2.1 Elo

Blind pairwise judgments for 220 professional tasks across 44 occupations; frozen/scaled anchor, Stirrup agent loop. Not saturated in the observed snapshot. (40 observations; proposed eligible; task_completion).

![GDPval-AA v2.1 Elo](charts/gdpval-aa-v2.1.svg)

### AA cost per Intelligence Index task

Weighted task cost; token prices, token use and cache behavior included. Cost measure, not quality. Fixed display scale 0–20 USD, not a probability. (40 observations; report-only; operational).

![AA cost per Intelligence Index task](charts/aa-cost-per-task-v4.3.2.svg)

### AA output speed

Median first-party API decode tokens/second after first chunk; excludes thinking/first-token time. Not saturated in the observed snapshot. (38 observations; report-only; operational).

![AA output speed](charts/aa-output-speed.svg)

### AA first-chunk latency

Median seconds to first response chunk; includes reasoning delay where reported. Not saturated in the observed snapshot. (38 observations; report-only; operational).

![AA first-chunk latency](charts/aa-first-chunk-latency.svg)

### Terminal-Bench 4.0 / Anthropic

Vendor-hosted release-table evaluation; effort/harness unspecified unless explicitly recorded. Self-reported release table. Full scaffold/effort missing for some rows; use only a verified comparable cohort. (4 observations; proposed eligible; task_completion).

![Terminal-Bench 4.0 / Anthropic](charts/terminal-bench-4.0-anthropic.svg)

### FrontierCode 1.1 Main / Anthropic

Merge-readiness score from vendor release tables; not Extended subset. Self-reported release table. Full scaffold/effort missing for some rows; use only a verified comparable cohort. (5 observations; proposed eligible; task_completion).

![FrontierCode 1.1 Main / Anthropic](charts/frontiercode-1.1-main-anthropic.svg)

### HLE / Anthropic with tools

Vendor HLE evaluation with tools; incompatible with AA text-only no-tools. Self-reported release table. Full scaffold/effort missing for some rows; use only a verified comparable cohort. (3 observations; proposed eligible; task_completion).

![HLE / Anthropic with tools](charts/hle-anthropic-tools.svg)

### HLE / Anthropic no tools

Vendor HLE no-tools protocol; task subset/grade details unverified. Self-reported release table. Full scaffold/effort missing for some rows; use only a verified comparable cohort. (2 observations; proposed eligible; task_completion).

![HLE / Anthropic no tools](charts/hle-anthropic-no-tools.svg)

### OSWorld 2.1 offline subset / Anthropic

Computer-use task completion, offline subset only. Self-reported release table. Full scaffold/effort missing for some rows; use only a verified comparable cohort. (3 observations; proposed eligible; task_completion).

![OSWorld 2.1 offline subset / Anthropic](charts/osworld-2.1-offline-anthropic.svg)

### OSWorld 2.1 partial / Anthropic

Partial-credit metric, separately versioned from offline task completion. Self-reported release table. Full scaffold/effort missing for some rows; use only a verified comparable cohort. (2 observations; proposed eligible; task_completion).

![OSWorld 2.1 partial / Anthropic](charts/osworld-2.1-partial-anthropic.svg)

### CursorBench 4.0 / Anthropic

Ambiguous multi-file tasks drawn from real Cursor sessions. Self-reported release table. Full scaffold/effort missing for some rows; use only a verified comparable cohort. (2 observations; proposed eligible; task_completion).

![CursorBench 4.0 / Anthropic](charts/cursorbench-4.0-anthropic.svg)

### SWE-bench Verified

500 human-filtered repository issue fixes. Public frontier clustering and contamination concerns; no verified scoped model rows collected. (0 observations; report-only; task_completion).

![SWE-bench Verified](charts/swe-bench-verified.svg)

### SWE-bench Pro

Harder multi-repository software issue resolution. No exact current scoped rows established from primary leaderboard. (0 observations; report-only; task_completion).

![SWE-bench Pro](charts/swe-bench-pro.svg)

### Aider Polyglot

225 Exercism edits across six programming languages. Opened board still reports historical generations; excluded from latest-only dataset and inactive. (0 observations; report-only; task_completion).

![Aider Polyglot](charts/aider-polyglot.svg)

### SWE-Lancer

Freelance software implementation and engineering-management tasks. No current scoped comparable values verified. (0 observations; report-only; task_completion).

![SWE-Lancer](charts/swe-lancer.svg)

### BrowseComp

Difficult web research with multi-step browsing. Scaffold and compaction choices matter; no comparable current scoped rows established. (0 observations; report-only; task_completion).

![BrowseComp](charts/browsecomp.svg)

### ARC-AGI-2

Novel visual-rule abstraction; pass rate depends on test-time compute. Latest frontier reports have shifted to ARC-AGI-3; no verified scoped version-2 cohort retained. (0 observations; report-only; task_completion).

![ARC-AGI-2](charts/arc-agi-2.svg)

### MRCR v2 / 8 needles / 512K–1M

Long-context multi-round coreference retrieval, eight needles, 512K–1M length bucket. No verified shared current scoped effort cohort; AA-LCR is a distinct proxy, not MRCR. (0 observations; report-only; task_completion).

![MRCR v2 / 8 needles / 512K–1M](charts/mrcr-v2-8needle-1m.svg)

### SimpleQA

Short factual-answer accuracy; hallucination/refusal must be interpreted separately. No current scoped rows verified; AA-Omniscience is retained as a separate benchmark. (0 observations; report-only; task_completion).

![SimpleQA](charts/simpleqa.svg)

### SEC-bench Pro

Long-horizon security investigation and patching across V8, SpiderMonkey and Linux. Credible public security benchmark; restricted-access/safeguard modes not comparable to ordinary harness review; no rows retained. (0 observations; report-only; task_completion).

![SEC-bench Pro](charts/sec-bench-pro.svg)

### AACR-Bench

Expert-verified multilingual repository-context automated code review. Relevant code-review benchmark; no current four-family generation scores verified. (0 observations; report-only; task_completion).

![AACR-Bench](charts/aacr-bench.svg)

### DeepSWE v1.1 / Google

Long-horizon repository engineering; vendor launch headline, scaffold/effort not stated. Self-reported; exact effort/scaffold unknown, so report-only until a compatible protocol is verified. (1 observations; report-only; task_completion).

![DeepSWE v1.1 / Google](charts/deepswe-1.1-google.svg)

### AutomationBench full completion / Google

Full tasks completed, not AA partial objective score. Self-reported; exact effort/scaffold unknown, so report-only until a compatible protocol is verified. (1 observations; report-only; task_completion).

![AutomationBench full completion / Google](charts/automationbench-full-google.svg)

### CWE-bench v1 / Google

Security vulnerability remediation, generation-v1 protocol; vendor launch headline. Self-reported; exact effort/scaffold unknown, so report-only until a compatible protocol is verified. (1 observations; report-only; task_completion).

![CWE-bench v1 / Google](charts/cwe-bench-v1-google.svg)

### Arena Text

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (8 observations; report-only; preference).

![Arena Text](charts/arena-text.svg)

### Arena Text Coding

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (8 observations; proposed eligible; preference).

![Arena Text Coding](charts/arena-text-coding.svg)

### Arena Text Hard Prompts

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (8 observations; proposed eligible; preference).

![Arena Text Hard Prompts](charts/arena-text-hard-prompts.svg)

### Arena Text Math

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (8 observations; proposed eligible; preference).

![Arena Text Math](charts/arena-text-math.svg)

### Arena Text Instruction Following

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (8 observations; proposed eligible; preference).

![Arena Text Instruction Following](charts/arena-text-instruction-following.svg)

### Arena Text Multi Turn

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (8 observations; proposed eligible; preference).

![Arena Text Multi Turn](charts/arena-text-multi-turn.svg)

### Arena Text Longer Query

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (8 observations; report-only; preference).

![Arena Text Longer Query](charts/arena-text-longer-query.svg)

### Arena Text Creative Writing

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (8 observations; report-only; preference).

![Arena Text Creative Writing](charts/arena-text-creative-writing.svg)

### Arena Text Expert

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (8 observations; proposed eligible; preference).

![Arena Text Expert](charts/arena-text-expert.svg)

### Arena Webdev

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (10 observations; proposed eligible; preference).

![Arena Webdev](charts/arena-webdev.svg)

### Arena Search

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (0 observations; proposed eligible; preference).

![Arena Search](charts/arena-search.svg)

### Arena Vision

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (7 observations; proposed eligible; preference).

![Arena Vision](charts/arena-vision.svg)

### Arena Image To Webdev

Human pairwise preference rating for this category; not task-completion accuracy. Higher rating means preferred outputs under the source settings. Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. (7 observations; proposed eligible; preference).

![Arena Image To Webdev](charts/arena-image-to-webdev.svg)

### Agent Arena Code net improvement

Causal net improvement estimated from real coding-agent sessions; not Elo or a raw pass rate. Report-only: causal treatment effect depends on assignment baseline and Arena tool stack. Five signal pipelines; 95% confidence intervals, not pairwise votes. (8 observations; report-only; observational_agent).

![Agent Arena Code net improvement](charts/arena-agent-code-net-improvement.svg)

### SimpleQA Verified / Epoch anti-abstention accuracy

1,000 closed-book factoid questions with Epoch anti-abstention instruction; raw correct-answer proportion, NOT Google F1 and NOT citation accuracy. Protocol changed 2026-08-27; use post-change evaluations only. No tools/search; useful factuality proxy, not lead-dossier validation. (8 observations; proposed eligible; task_completion).

![SimpleQA Verified / Epoch anti-abstention accuracy](charts/simpleqa-verified-epoch-anti-abstention.svg)

### BrowseComp / Mercor 130-question subset

130 public questions, four runs, pass@1; web-research agent, 100 steps, 3-hour limit, Gemini 3.5 Flash grader. Short-answer correctness, not citation/report accuracy or 15-minute quality. Source sample counts vary; missing-run/denominator policy undisclosed; uncertainty error is not a verified CI. (9 observations; proposed eligible; task_completion).

![BrowseComp / Mercor 130-question subset](charts/browsecomp-mercor-130.svg)

### BrowseComp Extended / Mercor 100-task set

Separate 100-task held-out Mercor set; no pooled 230-question score. Exact repeats, tools, time limit and grader unverified. Short-answer correctness, not citation/report accuracy or 15-minute quality. Source sample counts vary; missing-run/denominator policy undisclosed; uncertainty error is not a verified CI. (7 observations; report-only; task_completion).

![BrowseComp Extended / Mercor 100-task set](charts/browsecomp-mercor-extended-100.svg)

### DRACO / Anthropic Opus-4.6 regrading, September 2026

100 expert-rubric research reports; 980K token task budget, web search/fetch, programmatic tool calling, code execution; final report file graded five times by Opus 4.6. Self-reported rubric score. Changed grader can shift absolute scores 10–25 points relative to original Perplexity; no 15-minute quality or cost-from-log-chart claim. (15 observations; proposed eligible; task_completion).

![DRACO / Anthropic Opus-4.6 regrading, September 2026](charts/draco-anthropic-opus46-grader-2026-09.svg)

### WANDR / Anthropic frozen index, Opus-4.6 grader

500 wide-research tasks with structured entity facts and citations; FROZEN search/fetch index, 980K token budget; Opus 4.6 re-fetches cited evidence. Soft F1 balances supported fact precision and requested fact recall. Soft F1, not raw citation precision. Frozen corpus/Opus4.6 grader differs from original live-web/GPT5.4 protocol; no 15-minute claim. (15 observations; proposed eligible; task_completion).

![WANDR / Anthropic frozen index, Opus-4.6 grader](charts/wandr-anthropic-frozen-opus46-grader-2026-09.svg)

### DRACO / Fable 5.1 launch-card cohort

Earlier Fable launch-card evaluation; preserve independently from its later re-evaluation in Opus card. Same current model, distinct evaluation cohort: do not overwrite or average later Opus-card re-evaluation. (5 observations; report-only; task_completion).

![DRACO / Fable 5.1 launch-card cohort](charts/draco-anthropic-fable-card-2026-09-01.svg)

### BrowseComp / OpenAI Astra launch

Original BrowseComp vendor launch; maximum at any effort, research/API environment. Self-reported, exact effort undisclosed: report-only. Production system prompts/tools can differ; do not transfer to a named effort. (1 observations; report-only; task_completion).

![BrowseComp / OpenAI Astra launch](charts/browsecomp-openai-launch-2026-09.svg)

### HLE with tools / OpenAI Astra launch

Academic tool-enabled headline, maximum across any effort; not the AA no-tools subset. Self-reported, exact effort undisclosed: report-only. Production system prompts/tools can differ; do not transfer to a named effort. (1 observations; report-only; task_completion).

![HLE with tools / OpenAI Astra launch](charts/hle-openai-tools-launch-2026-09.svg)

### MRCR v2 / 8 needles / 512K–1M / OpenAI launch

Long-context retrieval; 512K–1M bucket, eight needles, maximum across any effort. Self-reported, exact effort undisclosed: report-only. Production system prompts/tools can differ; do not transfer to a named effort. (1 observations; report-only; task_completion).

![MRCR v2 / 8 needles / 512K–1M / OpenAI launch](charts/mrcr-v2-8needle-512k-1m-openai-launch.svg)

### BrowseComp-Plus fixed corpus

830 reasoning-intensive queries over ~100K verified documents; retriever/scaffold/grade must be retained. Maintainer CSV has 85 agent/retriever rows; no retained current model. Separate from live-web BrowseComp. (0 observations; report-only; task_completion).

![BrowseComp-Plus fixed corpus](charts/browsecomp-plus-fixed-corpus.svg)

### DeepResearch Bench I / RACE

100 PhD-level tasks, 50 English/50 Chinese, 22 fields; adaptive reference-based report-quality rubric. Maintainer CSV reports products/custom agents and older models; no exact retained base-model/scaffold cohort. (0 observations; report-only; task_completion).

![DeepResearch Bench I / RACE](charts/deepresearch-bench-i-race.svg)

### DeepResearch Bench I / FACT citation accuracy

FACT tests whether citations support cited claims; distinct from RACE report quality and effective citation count. No current retained base-model citation-accuracy row. Product score cannot be transferred to underlying model. (0 observations; report-only; task_completion).

![DeepResearch Bench I / FACT citation accuracy](charts/deepresearch-bench-i-fact-citation.svg)

### DeepResearch Bench II

Maintainer portal labels Bench I and Bench II separately; no matching current base-model result verified. Version/protocol difference unverified here; no values or cross-version score transfer. (0 observations; report-only; task_completion).

![DeepResearch Bench II](charts/deepresearch-bench-ii.svg)

### Deep Research Bench / FutureSearch

169 diverse frozen-web research tasks with curated answers; ReAct/RetroSearch, category-averaged scoring. Different benchmark from 100-task DeepResearch Bench. Board last updated 2026-06-10; no retained current models. (0 observations; report-only; task_completion).

![Deep Research Bench / FutureSearch](charts/deep-research-bench-futuresearch.svg)

### DeepConsult

102 business/consulting queries with paired research-report grading six times per query. ARI versus OpenAI Deep Research is a product comparison; no retained current base-model identity/protocol. (0 observations; report-only; task_completion).

![DeepConsult](charts/deepconsult.svg)

### DeepResearch Arena / seminar tasks

Research tasks grounded in academic seminars, over 10K tasks across 12 disciplines; paper defines research-specific grading. No exact retained current base-model results verified; distinct from sandbox Deep Research Arena and arena.ai. (0 observations; report-only; task_completion).

![DeepResearch Arena / seminar tasks](charts/deepresearch-arena-seminar.svg)

### Deep Research Arena / frozen sandbox

13-task diagnostic harness×backbone board; URL reachability gate multiplied by jury win rate. Board has 11 harnesses over two non-scope backbones; no retained model. URL reachability alone is not claim support. (0 observations; report-only; task_completion).

![Deep Research Arena / frozen sandbox](charts/deepresearch-arena-sandbox.svg)

### GAIA test set

General assistant questions requiring reasoning, web search, files and tools; three levels; board reports best submissions. Public table has agent ensembles/generic family labels and older versions; no unambiguous retained current model score. (0 observations; report-only; task_completion).

![GAIA test set](charts/gaia-test.svg)

### WebArena functional success

Self-hosted web tasks with programmatic end-state checks across shopping/forums/GitLab/CMS. Exact current model/observation-space/action-scaffold cohort missing. Web task actions differ from a read-only lead brief. (0 observations; report-only; task_completion).

![WebArena functional success](charts/webarena-functional.svg)

### WebVoyager

End-to-end multimodal browser navigation on live websites; evaluator/human-check settings matter. Maintainer release describes earlier models; no exact retained current class score established. (0 observations; report-only; task_completion).

![WebVoyager](charts/webvoyager.svg)

### Online-Mind2Web

300 live-web tasks on 136 sites; updated task set, human checks/WebJudge. Both primary tables inspected (13 and 20 rows); no exact retained current model. Generic labels not resolved by guessing. (0 observations; report-only; task_completion).

![Online-Mind2Web](charts/online-mind2web.svg)

### SimpleQA Verified / Google F1

1,000 short-answer factuality prompts; harmonic mean of overall accuracy and attempted-answer accuracy, not Epoch raw accuracy. Kaggle page did not expose current numeric rows to the reader; Epoch raw accuracy remains separately sourced. (0 observations; report-only; task_completion).

![SimpleQA Verified / Google F1](charts/simpleqa-verified-google-f1.svg)

### FRAMES

824 multi-hop Wikipedia questions evaluating factuality, retrieval and reasoning; search/RAG protocol must be specified. No exact current model/harness comparison verified; do not transfer DeepResearch product scores. (0 observations; report-only; task_completion).

![FRAMES](charts/frames.svg)

### FActScore atomic factual precision

Breaks generated text into atomic claims and measures supported proportion using a specified knowledge source. Framework verified; no current retained model measured with a specified evaluator/retriever/context cohort. (0 observations; report-only; task_completion).

![FActScore atomic factual precision](charts/factscore-atomic-precision.svg)

### AA suite-weighted output tokens/task

AA benchmark-weighted token use per Intelligence Index task; provider counts when available, canonical fallback otherwise. Not subscription quota burn. Operational benchmark-suite proxy only; no local quota calibration, lead-task latency, failure or rework measurement. (40 observations; report-only; operational).

![AA suite-weighted output tokens/task](charts/aa-output-tokens-per-task-v4.3.2.svg)

### AA suite-weighted reasoning tokens/task

AA benchmark-weighted token use per Intelligence Index task; provider counts when available, canonical fallback otherwise. Not subscription quota burn. Operational benchmark-suite proxy only; no local quota calibration, lead-task latency, failure or rework measurement. (40 observations; report-only; operational).

![AA suite-weighted reasoning tokens/task](charts/aa-reasoning-tokens-per-task-v4.3.2.svg)

### AA suite-weighted answer tokens/task

AA benchmark-weighted token use per Intelligence Index task; provider counts when available, canonical fallback otherwise. Not subscription quota burn. Operational benchmark-suite proxy only; no local quota calibration, lead-task latency, failure or rework measurement. (40 observations; report-only; operational).

![AA suite-weighted answer tokens/task](charts/aa-answer-tokens-per-task-v4.3.2.svg)

### AA suite-weighted derived decode seconds/task

AA benchmark-weighted decode-time estimate: output-token use divided by output speed and weighted across evaluations. Excludes first-token delay and overhead; not wall-clock agent task time. Operational benchmark-suite proxy only; no local quota calibration, lead-task latency, failure or rework measurement. (38 observations; report-only; operational).

![AA suite-weighted derived decode seconds/task](charts/aa-derived-decode-time-per-task-v4.3.2.svg)
