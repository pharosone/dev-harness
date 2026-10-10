# Benchmark sources and comparability

Observed 2026-10-10. This is Part A evidence and offline reporting. Model routing, runtime adapters and classifier evaluation remain design work requiring owner approval.

## Retained model scope

The final owner instruction limits publication to four families and the newest revision of every class in the newest generation. Earlier research remains scratch only. The 9 retained canonical IDs are below; reasoning effort is a result setting, not another model.

| Family | Retained current classes | Primary identity / availability source |
|---|---|---|
| Claude 5 | Fable 5.1, Opus 5.5, Sonnet 5.5, Haiku 5.5 | [Claude model catalog](https://platform.claude.com/docs/en/models/overview) |
| GPT-6 | Astra, 6.1 Sol, Luna | [OpenAI API catalog](https://developers.openai.com/api/docs/models), [Codex/Work model availability](https://learn.chatgpt.com/docs/models) |
| Gemini 4 | Argon; no other generation-4 class established | [Google announcement](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/) |
| Grok 4.7 | Grok 4.7; Fast is a serving variant of this same model | [Model catalog](https://docs.x.ai/developers/models), [4.7 developer guide](https://docs.x.ai/developers/grok-4-7) |

Claude earlier minor revisions and all Claude 4 are excluded. GPT-6 Sol is superseded by GPT-6.1 Sol for the retained Sol class; all GPT-5.x and retired Codex-specific variants are excluded. No separately named GPT-6-Codex model was verified: GPT-6 models themselves run in Codex. Gemini 3.8 Flash and 3.x Lite/Pro are prior-generation entries and excluded under the literal latest-generation rule. Older Grok revisions and every other vendor are excluded from models, results, extracts and charts.

Gemini/Grok are `runtime="planned"` with `planned_runtime="gemini"`/`"grok"`, not runnable today. Gemini CLI integration is planned; Argon itself has phased/trusted access. Availability does not imply account access. No cross-version `fallback_models` are declared: benchmark scores cannot be transferred between revisions.

## Price and context provenance

| Model | Input / output USD per Mtok | Context tokens | Price and context source |
|---|---:|---:|---|
| claude-fable-5-1 | 10 / 50 | 1000000 | [price](https://platform.claude.com/docs/en/models/overview), [context](https://platform.claude.com/docs/en/models/overview) |
| claude-opus-5-5 | 4 / 20 | 1000000 | [price](https://platform.claude.com/docs/en/models/overview), [context](https://platform.claude.com/docs/en/models/overview) |
| claude-sonnet-5-5 | 2 / 10 | 1000000 | [price](https://platform.claude.com/docs/en/models/overview), [context](https://platform.claude.com/docs/en/models/overview) |
| claude-haiku-5-5 | 0.1 / 0.5 | 1000000 | [price](https://platform.claude.com/docs/en/models/overview), [context](https://platform.claude.com/docs/en/models/overview) |
| gpt-6-astra | 10 / 50 | 1050000 | [price](https://developers.openai.com/api/docs/models/gpt-6-astra), [context](https://developers.openai.com/api/docs/models/gpt-6-astra) |
| gpt-6.1-sol | 2 / 10 | 1050000 | [price](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [context](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| gpt-6-luna | 0.1 / 0.5 | 1050000 | [price](https://developers.openai.com/api/docs/models/gpt-6-luna), [context](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| gemini-4-argon | 2 / 10 | 1000000 | [price](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/), [context](https://artificialanalysis.ai/models/gemini-4-argon) |
| grok-4.7 | 2 / 6 | 500000 | [price](https://docs.x.ai/developers/models), [context](https://docs.x.ai/developers/models) |

Prices are standard uncached text rates, excluding tools and serving-tier premiums. Haiku prices apply to prompts up to 100K; above that the official rate is $0.50/$2.50. Sonnet cache reads changed to $0.10/Mtok on October 7; cached rates are not the input price in this schema. [Haiku release/pricing](https://www.anthropic.com/claude-haiku-5-5).

Argon $2/$10 is INTRODUCTORY; the launch footnote states $4/$20 after the introductory period, with no expiry date established here. Its announcement describes a 1M **output** limit; its 1M **context** value comes separately from [AA model specifications](https://artificialanalysis.ai/models/gemini-4-argon). Grok 4.7 Fast uses faster infrastructure in Cursor/Grok Build, with the same underlying model; no new API model ID is invented. [Grok developer guide](https://docs.x.ai/developers/grok-4-7).

## Source ranking and observation dates

Primary independent evaluator/maintainer data is preferred to model-vendor release tables. All numeric rows here come from Artificial Analysis, official Arena boards, Anthropic release tables or the Google Argon announcement. No aggregator supplies a score. `self_reported=true` denotes vendor-hosted/reported evaluation, including an Anthropic measurement of GPT-6 Luna. It does not imply the measured model vendor ran that peer result.

`as_of` is observation date. `date_type="observation"` uses that same date; it is neither a release date nor an evaluation execution date. Vendor table `date_type="publication"` records the article date, with execution date unknown. `evaluation_date=null` is deliberate. Arena vote cutoffs/source updates are preserved separately. A current snapshot can contain old evaluations, so a clean staleness check only establishes source freshness, not rerun freshness.

## Artificial Analysis extraction

[Current methodology](https://artificialanalysis.ai/methodology/intelligence-benchmarking) identifies Intelligence Index **v4.3.2** and its ten evaluations. Older v4.1.1 values are not mixed into this index. HTML was fetched unauthenticated from public model-page URLs, public `self.__next_f.push` strings JSON-decoded, and matching model dictionaries read from their chart data. No private API, authentication or hidden endpoint was used. The text reader sometimes says “Not publicly available” where the same public HTML contains chart observations; retained extracts document exactly which fields supplied the numbers.

Only the 40 exact model/effort dictionaries belonging to the 9 retained canonical models were extracted. Each result records exact `source_model`, `effort`, `source_field`, URL and raw HTML SHA-256. [source-extracts.json](source-extracts.json) retains the selected numeric fields and settings for independent audit. Raw full pages remain in shared research scratch; excluded vendor/model dictionaries are not published.

Fraction fields were multiplied by 100 to use percent units; Elo and index values were preserved. No absent value becomes zero. No approximate best-effort interpolation is performed. Some lower-effort results beat higher effort: the source values are retained without smoothing.

Claude Opus/Sonnet/Fable AA rows use production safeguard fallback, explicitly reflected in source model names. This is part of the measured provider setup; it is unrelated to our empty `fallback_models` arrays. API benchmarks are not automatically scores for our Codex/Claude Code configuration.

AA Terminal-Bench **4.0**: 66 tasks, mini-swe-agent, three repeats per task, pass@1. AA Terminal-Bench **2.1**: 89 tasks, Terminus 2/E2B, three repeats. These have separate IDs and must not be merged. [4.0 protocol](https://artificialanalysis.ai/evaluations/terminalbench-4-0), [2.1 protocol](https://artificialanalysis.ai/evaluations/terminalbench-2-1).

AutomationBench-AA is guardrail-gated **objective completion**, not full-task completion. Google’s 51.3% AutomationBench headline is retained under a separate report-only full-completion ID. [AA methodology](https://artificialanalysis.ai/evaluations/automationbench-aa), [Google release](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/).

HLE-AA is the 2,158-question text-only May-2025 subset, no tools, GPT-5.6 Luna medium equality checker. Anthropic HLE with tools and no tools are separate source protocols and IDs. AA-LCR v1.1 is not MRCR. SciCode v1.0.1 is Python science coding, not polyglot editing, and AA currently marks it under review; report-only. General reasoning is a review proxy and does not demonstrate vulnerability detection.

AA-Briefcase v1.1 and GDPval-AA v2.1 Elo use fixed normalization endpoints 500/2500 (the AA displayed `(Elo-500)/2000` transform). Percent endpoints are 0/100. Operational cost/speed domains are fixed display ranges and are inactive for quality. Composite intelligence is reference-only to avoid counting its component scores twice.

## Exact effort example for the approval design

| Independent AA metric | Opus 5.5 high, default fallback | GPT-6 Astra medium |
|---|---:|---:|
| Terminal-Bench 4.0, percent | 56.5656565656566 | 49.4949494949495 |
| AutomationBench-AA objective completion, percent | 63.20677483186213 | 64.59498850561176 |
| HLE text-only/no tools, percent | 55.5607043558851 | 52.7340129749768 |

Sources: [Opus high](https://artificialanalysis.ai/models/claude-opus-5-5-high), [Astra medium](https://artificialanalysis.ai/models/gpt-6-astra-medium). These are exact recorded efforts. Opus has the higher terminal point estimate; Astra has the slightly higher objective-completion estimate. A routing decision also needs predeclared weights and compatibility rules; these rows are not live classifier/router evaluation.

## Arena evidence

Arena Chat Text overall plus coding, hard prompts, math, instruction following, multi-turn, longer query, creative writing and expert were fetched, along with WebDev, Vision, Search, Image-to-WebDev and actual Agent/Code. Only exact retained model names are mapped; GPT-6 Sol and older names are dropped. Missing Haiku/other rows remain missing.

Pairwise boards use `category="preference"`, unit Elo, and source **rating** interval endpoints (`ratingLower`/`ratingUpper`), never rank intervals. Confidence level for these rating intervals is unverified and explicitly null. Votes, preliminary release flags, board cutoff, original model key, effort and style settings are retained. Text and Vision style control is enabled. WebDev/Image-to-WebDev do not offer style control; field null with an explicit reason rather than a false setting. Every null has a source-specific explanation in `missing_metadata`.

Text coding measures preferences for code answers. WebDev measures preferences on web builds. Neither proves general repository task success. LongerQuery is report-only: longer prompts do not establish 100K–1M retrieval capability. Overall and creative-writing preferences are also reference-only. The separate approval design caps category-relevant pairwise preference at 15%; that cap is not runtime code here.

Search has **zero** retained rows, with an August 24 vote cutoff; no earlier-generation score was carried forward. A standalone Copilot-named leaderboard was not found. The actual [Agent Code board](https://arena.ai/leaderboard/agent/code) was found and retained: headline `avgScore.value` is **causal net improvement**, converted from fraction to percentage points, not Elo or raw completion rate. `avgScore.ci` supplies the symmetric 95% half-width. [Agent methodology](https://arena.ai/blog/agent-arena-methodology) explicitly defines 95% intervals; [signal definitions](https://arena.ai/resources/agent-arena-signals) document the updated pipelines.

Agent Code uses `category="observational_agent"`, report-only. Votes are null because it is not pairwise voting; `session_count` and `observation_count` preserve the source sample counts, and style control is `not_offered`. Baseline assignment and tool stack condition the effect estimate; do not add it to preference Elo or task pass rates.

| Official Arena board | Retained rows | Source cutoff/update |
|---|---:|---|
| [text](https://arena.ai/leaderboard/chat/text) | 8 | 2026-10-08T14:00:00.000Z |
| [text-coding](https://arena.ai/leaderboard/chat/text/coding) | 8 | 2026-10-08T14:00:00.000Z |
| [text-hard-prompts](https://arena.ai/leaderboard/chat/text/hard-prompts) | 8 | 2026-10-08T14:00:00.000Z |
| [text-math](https://arena.ai/leaderboard/chat/text/math) | 8 | 2026-10-08T14:00:00.000Z |
| [text-instruction-following](https://arena.ai/leaderboard/chat/text/instruction-following) | 8 | 2026-10-08T14:00:00.000Z |
| [text-multi-turn](https://arena.ai/leaderboard/chat/text/multi-turn) | 8 | 2026-10-08T14:00:00.000Z |
| [text-longer-query](https://arena.ai/leaderboard/chat/text/longer-query) | 8 | 2026-10-08T14:00:00.000Z |
| [text-creative-writing](https://arena.ai/leaderboard/chat/text/creative-writing) | 8 | 2026-10-08T14:00:00.000Z |
| [text-expert](https://arena.ai/leaderboard/chat/text/expert) | 8 | 2026-10-08T14:00:00.000Z |
| [webdev](https://arena.ai/leaderboard/code/webdev) | 10 | 2026-10-08T13:00:00.000Z |
| [search](https://arena.ai/leaderboard/chat/search) | 0 | 2026-08-24T20:00:00.000Z |
| [vision](https://arena.ai/leaderboard/chat/vision) | 7 | 2026-10-09T19:00:00.000Z |
| [image-to-webdev](https://arena.ai/leaderboard/code/image-to-webdev) | 7 | 2026-10-09T19:00:00.000Z |
| [agent-code](https://arena.ai/leaderboard/agent/code) | 8 | 2026-10-08T12:00:00.000Z |

## Requested benchmark gaps and inactive evidence

| Benchmark | Why no active comparison | Primary source |
|---|---|---|
| SciCode / AA v1.0.1 | AA flags SciCode under review; inactive until reliability review resolves. | [source](https://artificialanalysis.ai/evaluations/scicode) |
| IFBench / AA | Not saturated in the observed snapshot. | [source](https://artificialanalysis.ai/evaluations/ifbench) |
| GPQA Diamond / AA | Removed from AA Intelligence Index in v4.2; frontier saturation reduces discrimination; inactive. | [source](https://artificialanalysis.ai/evaluations/gpqa) |
| Terminal-Bench 2.1 / AA | Superseded by 4.0 for current routing; retain independently versioned observations only. | [source](https://artificialanalysis.ai/evaluations/terminalbench-2-1) |
| tau²-bench Telecom / AA | Legacy AA suite; replaced by tau³-Banking; no current scoped scores verified. | [source](https://artificialanalysis.ai/evaluations/tau2-bench) |
| LiveCodeBench / AA legacy | AA retired active reporting; exact historical sample window unknown. No routing use. | [source](https://artificialanalysis.ai/evaluations/livecodebench) |
| SWE-bench Verified | Public frontier clustering and contamination concerns; no verified scoped model rows collected. | [source](https://www.swebench.com/) |
| SWE-bench Pro | No exact current scoped rows established from primary leaderboard. | [source](https://labs.scale.com/leaderboard/swe_bench_pro) |
| Aider Polyglot | Opened board still reports historical generations; excluded from latest-only dataset and inactive. | [source](https://aider.chat/docs/leaderboards/) |
| SWE-Lancer | No current scoped comparable values verified. | [source](https://github.com/openai/SWELancer-Benchmark) |
| BrowseComp | Scaffold and compaction choices matter; no comparable current scoped rows established. | [source](https://github.com/openai/simple-evals) |
| ARC-AGI-2 | Latest frontier reports have shifted to ARC-AGI-3; no verified scoped version-2 cohort retained. | [source](https://arcprize.org/leaderboard) |
| MRCR v2 / 8 needles / 512K–1M | No verified shared current scoped effort cohort; AA-LCR is a distinct proxy, not MRCR. | [source](https://github.com/openai/mrcr) |
| SimpleQA | No current scoped rows verified; AA-Omniscience is retained as a separate benchmark. | [source](https://github.com/openai/simple-evals) |
| SEC-bench Pro | Credible public security benchmark; restricted-access/safeguard modes not comparable to ordinary harness review; no rows retained. | [source](https://github.com/SEC-bench/SEC-bench-Pro) |
| AACR-Bench | Relevant code-review benchmark; no current four-family generation scores verified. | [source](https://github.com/alibaba/aacr-bench) |
| DeepSWE v1.1 / Google | Self-reported; exact effort/scaffold unknown, so report-only until a compatible protocol is verified. | [source](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/) |
| AutomationBench full completion / Google | Self-reported; exact effort/scaffold unknown, so report-only until a compatible protocol is verified. | [source](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/) |
| CWE-bench v1 / Google | Self-reported; exact effort/scaffold unknown, so report-only until a compatible protocol is verified. | [source](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/) |
| Arena Search | Human preference; planned design caps relevant preference at 15%. Category samples, style control, CIs and votes matter; do not treat this as objective coding accuracy. | [source](https://arena.ai/leaderboard/chat/search) |

The older Aider leaderboard is still useful historical evidence but its opened rows are previous generations; omitted under owner scope. GPQA Diamond was removed from AA’s index in v4.2 and is inactive here because frontier discrimination is limited. Terminal-Bench 2.1 is superseded for the proposed active route. LiveCodeBench AA legacy reporting was retired, and its historical time window was not verified; no active comparison is claimed.

No blanket saturation claim is made about SWE-bench Pro, SWE-Lancer, ARC-AGI-2, MRCR, SimpleQA or security/review benchmarks: the limitation here is missing **verified comparable current scoped evidence**. Credible [SEC-bench Pro](https://github.com/SEC-bench/SEC-bench-Pro) and [AACR-Bench](https://github.com/alibaba/aacr-bench) were found; general-reasoning scores are not substitutes for their measurements. Google’s DeepSWE v1.1 77.9% and CWE-bench v1 68% are vendor-reported, with exact effort/scaffold unknown and report-only.

## Reproduce and refresh

1. Read the cited current model/effort pages and benchmark methodology before changing scores.
2. Record source observation date, exact model/version/settings/scaffold, URL, numeric source field and SHA-256; update selected extracts and JSON together.
3. Run `python3 scripts/benchmarks.py validate --as-of YYYY-MM-DD` and resolve all errors; review >60-day warnings.
4. Run `python3 scripts/benchmarks.py render --as-of YYYY-MM-DD` and inspect charts; render is offline and does not refresh scores.
5. Run `python3 -m unittest discover -s tests -p test_benchmarks.py -v` plus repository validation before committing explicit owned paths.

## Public HTML snapshot hashes

| Exact source URL | Observed | SHA-256 |
|---|---|---|
| [source](https://artificialanalysis.ai/models/gemini-4-argon) | 2026-10-10 | `864946c54722edc4845204c8d6e47572e6d29414d5e70a6058095e4abf44ebb8` |
| [source](https://artificialanalysis.ai/models/claude-sonnet-5-5-medium) | 2026-10-10 | `921dd47eec44a1759f420ad7d606dd634f8ffa65054a45714f9f3d0ee539c9e8` |
| [source](https://artificialanalysis.ai/models/claude-fable-5-1-low) | 2026-10-10 | `47b5ce26feeb260782c62eb7971f34ec48d40eca03b2ceb2a30d1b16d8c9920e` |
| [source](https://artificialanalysis.ai/models/gpt-6-1-sol-xhigh) | 2026-10-10 | `df8633b365c03e1b82131498eecdbc0ca3fb5b85d68aa434025acf220cdb8f67` |
| [source](https://artificialanalysis.ai/models/gpt-6-luna-xhigh) | 2026-10-10 | `190fdfc0da80c6fec8d3d2d0229fb5f6abf4499f7d3cc4887423cc51f0b68f9f` |
| [source](https://artificialanalysis.ai/models/gpt-6-astra-xhigh) | 2026-10-10 | `0ebbb12d8c8e1d6397eeeeea4ccc21bbd46dc727b7346c963cbe8e84a0c6af6d` |
| [source](https://artificialanalysis.ai/models/claude-haiku-5-5-high) | 2026-10-10 | `efb364e678f3f3939fe65dd1ed9ee65f6d4372db74ee729c43e657ff1dea7b19` |
| [source](https://artificialanalysis.ai/models/grok-4-7-low) | 2026-10-10 | `c1d8dd4ccbc2a3a6b752dd005ffa5a0ddff0241ba30f0cb44f7ae221f3d994e8` |
| [source](https://artificialanalysis.ai/models/claude-sonnet-5-5-high) | 2026-10-10 | `e4a6cf2cedf7f61cc85359b71a5f20eecc612d58c2cb7c9b6ed5567bd5fddbb1` |
| [source](https://artificialanalysis.ai/models/grok-4-7-high) | 2026-10-10 | `d4d345956dacc4060d74b23179cc558ffdc19b0f7c85123fdbf17b9d2828d083` |
| [source](https://artificialanalysis.ai/models/gpt-6-astra) | 2026-10-10 | `ca5ddec7cd1e567571645ecb8aff8f4f9fef63d9b8415a8152408f745058f9e1` |
| [source](https://artificialanalysis.ai/models/claude-opus-5-5) | 2026-10-10 | `3460f50f93d01c673c23b93a1b47010e3b8da6e7ebdc6b2e0a540a539fb23d28` |
| [source](https://artificialanalysis.ai/models/gpt-6-luna-medium) | 2026-10-10 | `50eefdfacb130b660413d941a242812a14fcd63b39e41041e94af60996967f5b` |
| [source](https://artificialanalysis.ai/models/claude-fable-5-1-medium) | 2026-10-10 | `a5f135b971f2d3dc5d666c3e64908977fed652ac4d59dca1ca9f249520b91c74` |
| [source](https://artificialanalysis.ai/models/gpt-6-1-sol-high) | 2026-10-10 | `973cd096b878b836c114c4d29db994b01139093e328072a303334b2a20dc3b00` |
| [source](https://artificialanalysis.ai/models/claude-fable-5-1) | 2026-10-10 | `9b9d7193b41a766f771ff0f50ec36986dd9fb02a0120ce0bdf3ed42b1c9e8b38` |
| [source](https://artificialanalysis.ai/models/claude-haiku-5-5-xhigh) | 2026-10-10 | `1439522b81c85838f17163b62a12fb9b50b93b428bca9b5bd85b3a1ddd974598` |
| [source](https://artificialanalysis.ai/models/claude-haiku-5-5-low) | 2026-10-10 | `f460fc95269d42e02d32dc49d2ec193640752ab0c4f5d1c94195705b1726a74f` |
| [source](https://artificialanalysis.ai/models/claude-opus-5-5-low) | 2026-10-10 | `e7de882fcd04f13e4e5bffcba561c58afd98fc83fd90d1f95cce434753db6c5f` |
| [source](https://artificialanalysis.ai/models/grok-4-7) | 2026-10-10 | `0770f3c1f52d48a0d4e89088f1e672d54b13b0379d6a921dbbc6e78f201c762f` |
| [source](https://artificialanalysis.ai/models/gpt-6-luna-non-reasoning) | 2026-10-10 | `a2ff95ae101b82c957e71ed5cc06563523178999275bd9dbe89f66804b36d317` |
| [source](https://artificialanalysis.ai/models/gpt-6-luna) | 2026-10-10 | `9dfeb54c276dad6507e17de767c7d585668c7f6ee7e50fef421ff4031a8c2425` |
| [source](https://artificialanalysis.ai/models/claude-haiku-5-5) | 2026-10-10 | `52c0dceb790ed1ed44d1ab522e57b4260cbef7ab4922d2c2edfe6c34a13f3ab6` |
| [source](https://artificialanalysis.ai/models/gpt-6-luna-high) | 2026-10-10 | `91a2e1acf1c17774098ec2db2ecf4bf65c85fad37bd05515c2d5e6e240d4bcb3` |
| [source](https://artificialanalysis.ai/models/claude-sonnet-5-5-xhigh) | 2026-10-10 | `e876b1f92c68766639177b4c5456402709146a105f8b809db606db6a911a2166` |
| [source](https://artificialanalysis.ai/models/claude-fable-5-1-xhigh) | 2026-10-10 | `5fbb63afd1c8fa89161202df6740a356bcceb0dffaff8ba6ebe45188622af03d` |
| [source](https://artificialanalysis.ai/models/claude-fable-5-1-high) | 2026-10-10 | `1a7139eeb353a69a9d2a6f9f98ee84348d680ce429ba16b10e2660dc13935e36` |
| [source](https://artificialanalysis.ai/models/gpt-6-1-sol-medium) | 2026-10-10 | `540c3c92f7fc8fb24a58ca54c43ef55dfcea0217b24f585be07b697f98367932` |
| [source](https://artificialanalysis.ai/models/gpt-6-astra-low) | 2026-10-10 | `f5050c527f0518dd257b856c4427974ada737341162edb6d4b2f1b7699127ee2` |
| [source](https://artificialanalysis.ai/models/claude-haiku-5-5-medium) | 2026-10-10 | `00beda53b2c74bbd247d320fb159d53a0bdd5193d896fdb2b94ec6b2ad56d712` |
| [source](https://artificialanalysis.ai/models/claude-sonnet-5-5) | 2026-10-10 | `2edbf3ab79df2827be6d81b21f7525809f3004fa4d15382efc384b0d98ce9ae6` |
| [source](https://artificialanalysis.ai/models/gpt-6-1-sol) | 2026-10-10 | `e789012231355e1f44933d0a756dda43e64b1e7e8e140a84611d6ceb19c18319` |
| [source](https://artificialanalysis.ai/models/claude-sonnet-5-5-low) | 2026-10-10 | `f0f1dab051b692faddda311565e66a3e0433b89dfdf5829365d2db928a90897a` |
| [source](https://artificialanalysis.ai/models/gpt-6-luna-low) | 2026-10-10 | `67a26bfe2fd64747a2b0935c7933aa674fd4bbee1160fcfeb39ca87ed85558d2` |
| [source](https://artificialanalysis.ai/models/claude-opus-5-5-xhigh) | 2026-10-10 | `26e9d0f08388ae9367fd36166980ed25b5acc0a100ca35fd6d3070084fee2199` |
| [source](https://artificialanalysis.ai/models/gpt-6-astra-high) | 2026-10-10 | `c4fb14e9d97fbf87d4239545948f50d281c73162e4f9dc17888014f6a3618596` |
| [source](https://artificialanalysis.ai/models/gpt-6-1-sol-low) | 2026-10-10 | `2a3a85168f8be2295a784609df975fa3cb70165c00eef1fbf8ec2630c177c6ec` |
| [source](https://artificialanalysis.ai/models/gpt-6-astra-medium) | 2026-10-10 | `73f1e32846674607f16632dfee251aa52563c1797ddd471f2a7f558a9061f501` |
| [source](https://artificialanalysis.ai/models/claude-opus-5-5-medium) | 2026-10-10 | `afea1b7eb62bfd3567ca5aae4c6c0f8dcdb8f039e01b626ff5e47a71b12611fa` |
| [source](https://artificialanalysis.ai/models/claude-opus-5-5-high) | 2026-10-10 | `449a8a69e2a16de5e0882d2b1335cd834c7572ce948adcf0cad81ee094c373e6` |
| [source](https://arena.ai/leaderboard/chat/text) | 2026-10-10 | `51dcec99ba9c6914cd37c33c9f1a0bbc5917dc60e32121ac305d8b6685f34cac` |
| [source](https://arena.ai/leaderboard/chat/text/coding) | 2026-10-10 | `bca063add6d9cd7d34133c64624cf4efbe4916bb87d12cafa5208d779b31e79b` |
| [source](https://arena.ai/leaderboard/chat/text/hard-prompts) | 2026-10-10 | `0feb85c62124fed27e1145f669b81d3ed582ca0f1a59ce7817cc81127d5dc5c8` |
| [source](https://arena.ai/leaderboard/chat/text/math) | 2026-10-10 | `2c834014edebad6de0276e716516c0b416cd084757aeb765a34f1fd0e1f1923b` |
| [source](https://arena.ai/leaderboard/chat/text/instruction-following) | 2026-10-10 | `def3ee9fe343c320f5b6073f185678a44e925ca125e32607e4ea4be8bcf762ec` |
| [source](https://arena.ai/leaderboard/chat/text/multi-turn) | 2026-10-10 | `282bcf52ea46a9ef65abaebaf329908dc655bbdca5a3c29ff30c8ebbd492d919` |
| [source](https://arena.ai/leaderboard/chat/text/longer-query) | 2026-10-10 | `a5082ad4cb8af826985779f4561966191afffe3fd2dbcb01ac90d2656597b5ee` |
| [source](https://arena.ai/leaderboard/chat/text/creative-writing) | 2026-10-10 | `24cff70c7dc24a6f146b1c049b10a70e26cb58ae0ecde472f550cf61af9c6569` |
| [source](https://arena.ai/leaderboard/chat/text/expert) | 2026-10-10 | `41c0d0efb211dbb28e44cdbd6fd600d31d2ec61b52485e3a8315e4c3d675791e` |
| [source](https://arena.ai/leaderboard/code/webdev) | 2026-10-10 | `9527254931f4d0bbec27f06dccc16ee5fd4f05ce543c9d5b804b14788ff44a0e` |
| [source](https://arena.ai/leaderboard/chat/vision) | 2026-10-10 | `4466cbcf7d896c3c8ddfe4c967882db339e5dda23779406ba52ab7115ce271cb` |
| [source](https://arena.ai/leaderboard/code/image-to-webdev) | 2026-10-10 | `22558359d333bca1bf7987ed2dba62d0e9f0f341b07df948f4dfcbbbec443915` |
| [source](https://arena.ai/leaderboard/agent/code) | 2026-10-10 | `584b2e4343ad534d708a6aa6419409301052ed6536006fc3f4ccd432e5657c8c` |

## Deep web research addition — taxonomy design v1.1

The owner added a read-only B2B lead-research workflow: 10–15 minutes searching company sites, changelogs, trust centers, jobs, news and public professional information, returning verified facts, quotes and a supported hook. This dataset adds the `web_research` trait and evidence; it does **not** run lead/model evaluations or establish that deadline as a measured performance condition. Root owns taxonomy.json and the recommendation/validation design.

This addition supersedes earlier generic browsing-gap statements when a **separately named current protocol** now has evidence. The generic full BrowseComp, product-level research/citation boards, and unmatched current model/scaffold cells remain explicit gaps. Final data: 9 models, 79 benchmark IDs, 790 observations (728 independent, 62 vendor-hosted), including 62 new observations (24 independent, 38 vendor-hosted).

### Independent current model evidence

Epoch’s [public download page](https://epoch.ai/benchmarks/use-this-data) links the [benchmark ZIP](https://epoch.ai/data/benchmark_data.zip). Eight retained rows were read from `simpleqa_verified.csv`, selecting exact model/version/effort IDs. The source provides evaluation start timestamps and standard errors; these are preserved, without deriving confidence intervals. [SimpleQA Verified methodology](https://epoch.ai/benchmarks/simple-qa-verified) changed its anti-abstention prompt on August 27. Every retained run starts after that change. Epoch scores raw answer accuracy; Google’s [official F1 protocol](https://www.kaggle.com/benchmarks/deepmind/simpleqa-verified) has its own gap ID. This is **closed-book knowledge**, not browsing or claim-level citation precision.

| Exact source model/effort | Epoch raw accuracy (%) | Standard error (percentage points) | Evaluation start date |
|---|---:|---:|---|
| claude-haiku-5-5_max | 23.8 | 1.347359 | 2026-10-09 |
| gpt-6.1-sol_max | 73.9 | 1.389504 | 2026-09-29 |
| claude-sonnet-5-5_max | 46.5 | 1.578050 | 2026-09-29 |
| gpt-6-luna_max | 41.4 | 1.558354 | 2026-09-22 |
| grok-4.7_xhigh | 56.0 | 1.570499 | 2026-09-22 |
| claude-opus-5-5_max | 72.2 | 1.417452 | 2026-09-22 |
| claude-fable-5-1_max | 70.8 | 1.438551 | 2026-09-01 |
| gpt-6-astra_max | 75.6 | 1.358855 | 2026-08-30 |

Mercor’s [130-question public subset](https://www.mercor.com/apex/oss-benchmarks/oss-browsecomp-leaderboard/) and [100-task Extended set](https://www.mercor.com/apex/mercor-extended/ots-held-out-browsecomp-leaderboard/) provide 16 retained rows. [Original methodology](https://www.mercor.com/apex/methodology/) states four runs, pass@1, a web-research agent, 100 steps, three hours and Gemini 3.5 Flash grading. It is **not the full 1,266-question BrowseComp**. A dedicated Extended protocol was not established; its repeats/harness/tools/grader/time remain null, and Extended is report-only.

`reported_error_pp` retains Mercor’s displayed ±error. Its estimator/confidence level are unknown; `confidence_interval` and `confidence_level` remain null. The renderer labels this as unverified error and does not draw verified-CI lines for it. `n_samples` is a source denominator/sample-count field, not successes. Sol’s 496 samples do not mean 496/520 successes; 520 is the nominal public total. Missing-run causes and denominator rules are undisclosed. Do not impute failures, pool subsets or treat point estimates as a controlled paired ranking.

| Canonical model / effort | Public 130 accuracy | Published ±error; samples | Extended 100 accuracy | Published ±error; samples |
|---|---:|---|---:|---|
| claude-fable-5-1 / high | 78.3 | 6.1; 520 | absent | — |
| claude-fable-5-1 / max | 85.2 | 5.3; 493 | 63.3 | 8.5; 377 |
| claude-haiku-5-5 / max | 56.3 | 7.1; 520 | 27.5 | 7.2; 396 |
| claude-opus-5-5 / max | 88.5 | 5; 517 | 60.5 | 8.4; 400 |
| claude-sonnet-5-5 / max | 86.6 | 5; 504 | 73 | 8.1; 338 |
| gpt-6-astra / xhigh | 94.2 | 3.2; 520 | absent | — |
| gpt-6-luna / max | 83.3 | 5.4; 520 | 47.2 | 8.9; 400 |
| gpt-6.1-sol / max | 95.4 | 3.1; 496 | 67.7 | 8.1; 400 |
| grok-4.7 / xhigh | 75.8 | 6.3; 518 | 39.5 | 8; 398 |

Gemini 4 Argon has neither Mercor row; Astra has no retained Extended row. All dates are observation snapshots because execution/cutoff dates were not given. Efforts are literal max/high/xhigh; no max result was assigned to high/medium. Fable high and max remain different observations.

### Vendor deep research and cited-fact evidence

Root supplied publisher PDFs and visually checked their annotated score points. [Opus card](https://www.anthropic.com/claude-opus-5-5-system-card), [Sonnet card](https://www.anthropic.com/claude-sonnet-5-5-system-card), [Fable/Mythos card](https://www.anthropic.com/claude-fable-5-1-mythos-5-1-system-card). Results cite the final publisher PDF URL, its SHA-256, page and figure. No price was estimated from logarithmic cost axes.

DRACO: 100 tasks, 980K token budget, web search/fetch, programmatic tool calling and code execution; only the final report file is graded. Opus 4.6 supplies five independent MET/UNMET rubric gradings averaged into a normalized score. This changed grader can shift absolute scores 10–25 points relative to original Perplexity grading. The Opus-card later re-evaluation of Fable and the earlier Fable-card results are separate IDs/cohorts; no overwriting or averaging. The Sonnet card repeats the five Opus scores and describes the same protocol; repeated Opus entries were omitted, while Sonnet source/page provenance remains explicit.

WANDR: 500 tasks requiring many entities/facts/citations, 980K token budget, a **frozen** search/fetch index and Opus 4.6 grader that re-fetches cited URLs. The score is soft F1 balancing supported-fact precision and requested-fact recall, **not raw citation precision**. It differs from original Perplexity live-web/GPT-5.4 grading. No measured 15-minute deadline is claimed.

| Model | Effort | DRACO later Opus/Sonnet-card cohort | WANDR frozen-index soft F1 |
|---|---|---:|---:|
| claude-opus-5-5 | low | 72.5 | 31.2 |
| claude-opus-5-5 | medium | 83.9 | 62.8 |
| claude-opus-5-5 | high | 85.0 | 67.3 |
| claude-opus-5-5 | xhigh | 86.7 | 71.3 |
| claude-opus-5-5 | max | 87.4 | 72.3 |
| claude-fable-5-1 | low | 84.2 | 63.3 |
| claude-fable-5-1 | medium | 85.7 | 64.5 |
| claude-fable-5-1 | high | 86.5 | 66.7 |
| claude-fable-5-1 | xhigh | 86.9 | 67.7 |
| claude-fable-5-1 | max | 87.7 | 68.7 |
| claude-sonnet-5-5 | low | 64.6 | 10.0 |
| claude-sonnet-5-5 | medium | 71.2 | 29.9 |
| claude-sonnet-5-5 | high | 81.0 | 56.3 |
| claude-sonnet-5-5 | xhigh | 85.7 | 66.6 |
| claude-sonnet-5-5 | max | 87.0 | 70.0 |

Earlier Fable-card DRACO low/medium/high/xhigh/max: 84.6/85.3/85.4/87.1/87.7, retained report-only under its own cohort. This is a current model’s different evaluation, not an older-model score.

OpenAI [Astra announcement](https://openai.com/index/gpt-6-astra/) reports BrowseComp 91.5%, HLE with tools 57.2%, and MRCR v2 eight-needle 512K–1M 96.3%. It explicitly says maximum at **any** effort, in research/API environments with prompts/tools that can differ from production. These three rows have `effort=null` and are report-only. No “Astra max 91.5” assertion is made. Direct HTML download returned 403 but the web tool opened the primary page; no raw snapshot hash was invented.

### Required independent board audit and explicit gaps

| Primary benchmark/source | Current retained-model finding |
|---|---|
| [BrowseComp-Plus maintainer data](https://huggingface.co/datasets/Tevatron/BrowseComp-Plus-results) | Public agent CSV: 85 model/retriever rows, none from retained latest classes. Fixed ~100K corpus/830 queries; separate retriever/scaffold effects. Viewer schema error was bypassed by reading its published CSV, without running evals. |
| [DeepResearch Bench I/II portal](https://muset-ai-deepresearch-bench-leaderboard.hf.space/), [paper/site](https://deepresearch-bench.github.io/) | Products/custom agents and old versions; no exact retained base-model row. RACE report quality, FACT citation accuracy and Bench II have separate gap IDs. |
| [FutureSearch DRB](https://drb.futuresearch.ai/) | A different benchmark: 169 frozen-web tasks; board updated June 10, no latest retained models. Forecasting BTF-3 Astra is not DRB evidence and was not imported. |
| [DeepConsult](https://github.com/youdotcom-oss/ydc-deep-research-evals) | 102 consulting/business prompts, six paired judgments; ARI versus OpenAI Deep Research is a product comparison, not base-model evidence. |
| [Seminar DeepResearch Arena](https://arxiv.org/abs/2509.01396) / [sandbox Deep Research Arena](https://www.deepresearcharena.com/) | Distinct benchmarks. Seminar paper lacks exact current rows; sandbox board has 11 harnesses × two non-scope backbones, 13 diagnostic tasks. URL reachability is not semantic citation support. |
| [GAIA](https://gaia-benchmark-leaderboard.hf.space/) | Public 100-row table inspected: no exact current retained model. Ensembles and generic family labels cannot be mapped to a base model. |
| [WebArena](https://webarena.dev/og/) / [WebVoyager](https://github.com/MinorJerry/WebVoyager) / [Online-Mind2Web](https://huggingface.co/spaces/osunlp/Online_Mind2Web_Leaderboard) | Browser action benchmarks; current exact scaffold/class gap. Online-Mind2Web’s two primary tables (13 and 20 rows) had no exact scoped match. |
| [FRAMES](https://huggingface.co/datasets/google/frames-benchmark) / [FActScore](https://github.com/shmsw25/FActScore) | Dataset/framework verified; no current exact-model cohort. Neither is filled with product-level or generic factuality scores. |
| [Arena Search](https://arena.ai/leaderboard/chat/search) | Previous verified empty current-scope snapshot remains empty; no prior-model transfer. |

`web_research` is added only to appropriate existing HLE-tools, factual accuracy/hallucination, BrowseComp, SimpleQA and Arena Search entries. Closed-book AA/Epoch factuality is labelled a **proxy**; HLE no-tools remains separate and does not receive the new trait. Multiple affinity labels do not authorize double weighting the same observation; root’s design handles scoring groups.

### Additional public source hashes

| Source | SHA-256 |
|---|---|
| [source](https://epoch.ai/data/benchmark_data.zip) | `cc6edbeff62db8bbc7ee2a88cb06f8b4e0eb4e0cf8110de7dfe1bdcfa0a7bbc9` |
| [source](https://www.mercor.com/apex/oss-benchmarks/oss-browsecomp-leaderboard/) | `06c804f05077dbdcf950426ce73994fcf01152be625316806ea478a9261cec6d` |
| [source](https://www.mercor.com/apex/mercor-extended/ots-held-out-browsecomp-leaderboard/) | `a4d49e6067ee3c3ee56776af5f75af5e004de344cfbb9a0fc011402cf39eaac3` |
| [source](https://www-cdn.anthropic.com/fc1b44717c85dc068bc6ba5024219938094694bd/Claude%20Opus%205.5%20System%20Card.pdf) | `7311c9c6bbb16d012f1c12c7418b05949fcf7ae3e30d2c40f22050074b2a7378` |
| [source](https://www-cdn.anthropic.com/870c8f525702625d2c62fc6dd04c857e3250bec1/Claude%20Sonnet%205.5%20System%20Card.pdf) | `89cfd6a6d3aa6148eaf23f4a53006950ee7746009f381237c62920c720725785` |
| [source](https://www-cdn.anthropic.com/0339e6a7c5c7b87f5c07798616dc32c215d14235/Claude%20Fable%205.1%20&%20Claude%20Mythos%205.1%20System%20Card.pdf) | `b0d59edc7a60eef32a879c13d713cce60c3fefd7e6b5183afdc8b835af3c8c39` |
| [source](https://openai.com/index/gpt-6-astra/) | Not retained; primary web page opened, direct download 403 |

Epoch AI data reused with attribution under its documented CC BY terms. Maintainer source manifests and scoped numerical extracts are retained in source-extracts.json; full HTML/CSV/PDF/PNG evidence remains in shared scratch. No costly Claude/Codex subprocess, live lead run, benchmark eval, classifier call or model-price budget was spent.

## Per-effort resource evidence and curves

The evidence revision makes model/effort settings first-class reporting rows in [efforts.json](efforts.json), with all six categorical slots (`none`, `low`, `medium`, `high`, `xhigh`, `max`) visible for each of the nine retained models. This is evidence and charts only; no routing, quality-band selection, expected-total-cost policy or live model call is implemented.

**Support audit:** 54 slots: 40 API-documented supported settings, eight unsupported settings and six unverified Argon settings. AA publishes 40 exact-effort observations. The counts coincide because Grok medium is documented but unmeasured, while Argon high is measured by AA but its complete publisher control matrix is unverified. The publisher API support table does not prove a particular local CLI flag or account entitlement. Gemini/Grok runtimes remain planned.

| Model group | Documented API controls | Source |
|---|---|---|
| Claude Fable 5.1 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5 | low, medium, high, xhigh, max | [Claude effort documentation](https://platform.claude.com/docs/en/build-with-claude/effort) |
| GPT-6 Astra / GPT-6.1 Sol | low, medium, high, xhigh, max; none unsupported | [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| GPT-6 Luna | none, low, medium, high, xhigh, max | [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| Grok 4.7 | low, medium, high, xhigh; reasoning cannot be disabled | [Grok controls](https://docs.x.ai/developers/model-capabilities/text/reasoning) |
| Gemini 4 Argon | Complete API effort matrix unverified; AA reports high | [Publisher announcement](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/), [AA observed setting](https://artificialanalysis.ai/models/gemini-4-argon) |

The Claude page explicitly describes all five levels per current model; turning off thinking/between-tool modes is a separate control and is not fabricated as a `none` effort tuple. Effort labels are behavioral settings and are not equal compute budgets across models. GPT-6 Luna supports `none`, whereas Astra/Sol do not. Grok medium is a real documented setting and stays visible as an evidence gap; Grok max is unsupported. Account/client controls still require local verification in the root-owned design.

**Numerical provenance:** the same 40 public AA model/effort snapshots already cited in source-extracts.json expose `intelligenceIndexOutputTokensPerTask.{output,reasoning,answer}`, `intelligenceIndexTimePerTask`, `canonicalIntelligenceIndexTokenCount` and `intelligenceIndexCostPerTask`. Selected exact fields, URLs and original HTML SHA-256 values are added under `source-extracts.json.efforts`. Dates remain source observations on 2026-10-10, not benchmark execution dates. There are 120 token/task observations and 38 derived-time observations; four new operational benchmark IDs are inactive for quality routing. Missing time values are GPT-6 Luna medium and Gemini 4 Argon high; they remain null, without deriving them from another effort or current speed.

[AA token methodology](https://artificialanalysis.ai/methodology/intelligence-benchmarking) uses provider API token counts when available, with a canonical-tokenizer fallback when unavailable. Its [model-page token-use definition](https://artificialanalysis.ai/models/claude-opus-5-5-high#token-use) weights each benchmark’s output tokens by index weight and divides by task count excluding repeats. Output, reasoning and answer tokens/task are suite proxies, not per-call caps or a measured subscription charge. Source fields named `canonicalIntelligenceIndexTokenCount` are retained as raw suite totals; their totals are not divided by an assumed task count to produce input/task. Input tokens/task remains null.

**Time definition verified:** the [AA model-page speed section](https://artificialanalysis.ai/models/claude-opus-5-5-high#speed) defines time per Intelligence Index task as benchmark-weighted output-token use divided by output speed. The chart describes weighted decode time excluding first-token delay and overhead; the underlying `intelligenceIndexTimePerTask` is in seconds. This is a **source-derived decode estimate**, not measured wall-clock agent-task duration. It excludes first-token delay and overhead, and is separate from the existing first-chunk latency metric. It is not a 12-minute lead-research runtime. The source field is retained directly; this code does not reconstruct it by dividing separately rounded aggregate values.

**Cost definition:** [AA cost/task definition](https://artificialanalysis.ai/models/claude-opus-5-5-high#cost) weights each evaluation’s costs across input, cache hit, cache write, reasoning and answer tokens. Its methodology combines source token counts with live typical cache-hit measurements rather than treating the evaluation’s one-off cache behavior as universal. This API-cost proxy is not Claude Max or Codex subscription quota charges. Quota burn, actual lead wall time and failure/rework probability remain null and `quota_calibration_status=unmeasured`; root owns the expected-total-cost design and future calibration.

Figures in [effort-curves.md](../../docs/benchmarks/effort-curves.md) show fixed 0–100 AA Intelligence, raw weighted token/task components, derived decode seconds/task and API USD/task. Each model has its own categorical effort plot. No model-best normalization, monotonic smoothing, missing-effort interpolation or cross-model effort equality is used. Lines only join adjacent measured categories as a visual guide; a missing category breaks the line. The [compact CSV](../../docs/benchmarks/effort-curves.csv) retains every slot and its support/measurement/method flags. Unsupported and unverified settings remain distinguishable from supported-but-unmeasured settings.
