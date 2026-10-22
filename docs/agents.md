# Agents and orchestration

Five agents share one `MatchState`. Each has an offline mode that produces deterministic output without a model, used in tests, in CI and as a fallback.

| Agent | Online | Offline |
| --- | --- | --- |
| Analyst | rules only: live triggers and player tags | the same |
| Tactician | Foundry model: briefing and recap as structured output | template rules |
| Verifier | deterministic layer plus a model support check | deterministic layer |
| Watcher | deterministic verdicts plus a model-phrased sentence (guarded) | template sentence |
| Presenter | model rewrites of model-written content into each audience and language | phrase catalogue |

## Shared match state

`state.MatchState` is the single state object every agent reads and writes:

- **Events and snapshots** seen so far, plus the match clock.
- **The `SignalSet`** computed from them by `refresh_signals()`, using the same `compute_signals` the analytics tests cover.
- **The verified `Briefing` and its `Prediction`s.** The watcher will update their statuses in M4.
- **Cues** for the presenter, from M4 on.

An agent can only cite a signal that exists in `state.signals`. At half-time the state holds only first-half data, so a second-half ID like `sig-xg-HAR-p2` is rejected as unknown.

## Verified writing workflow (Microsoft Agent Framework)

`orchestration/verified.py` is shared by the half-time briefing (`halftime.py`) and the full-time recap (`recap.py`). Each one plugs in a small `WritingTask` that supplies its checks, claims, pruning, template and output type. For the briefing, it builds a `WorkflowBuilder` graph with three executors:

```
HalftimeRequest -> [tactician] --DraftMessage (N parallel candidates)--> [verifier] --Briefing--> output
                        ^                                                   |
                        +---------------- RejectionMessage -----------------+   up to 2 retries
                                                                            |
                                                  FallbackMessage ----------+--> [fallback] --Briefing--> output
```

- **Tactician.** Online, an Agent Framework `Agent` on a Foundry `gpt-5-mini` deployment (`FoundryChatClient`, Responses API, reasoning effort `minimal`) drafts the briefing as strict structured output (`BriefingOut`), which is converted into the `BriefingDraft` contract. Each round writes `SECOND_ACT_PARALLEL_DRAFTS` (default 2) independent drafts concurrently. The prompt contains:
  - the half-time score, the teams and their starting formations;
  - every team-level first-half signal, labelled with whose action it measures;
  - the analyst's key contrasts;
  - the analyst's rule-based candidate adjustments, which the model may keep, adapt or replace;
  - per-window trends, marked as context only and not citable;
  - player signals.

  Offline, the tactician is `template_briefing`, a set of fixed rules over the signals.
- **Verifier.**
  - **Layer 1 (deterministic, `agents/verifier.py`):**
    - every cited ID exists;
    - every number in the text is a cited value (to tolerance or rounding), or a difference, ratio or percentage change between two cited values;
    - every prediction is evaluable: allowed metric, valid scope, threshold 5–80%, evaluated after minute 60–85, ID format;
    - every prediction has a baseline thick enough to test: at least 6 first-half events for count metrics, at least 0.5 for xG, and values in at least 5 windows for rates and ratios. Below that, one or two events would decide the result. The template applies the same rule when it proposes predictions;
    - structure: two teams, 1–3 points per section, 2–3 predictions each.
  - **Layer 2 (online only):** one batched model call judges whether each claim's meaning follows from its cited values. It sees the metric definitions with the evidence. A failure in layer 2 itself is logged but doesn't block a draft that already passed layer 1.
- **Repair before retry.** When a draft fails only on specific points or predictions, the verifier removes those claims instead of discarding the draft. It does this only if each team keeps at least one point per section and two predictions. A candidate rejected by the model check is pruned and accepted, because its other claims were already judged supported. A candidate rejected by the deterministic layer is pruned and then model-checked, if the budget allows. Removed claims, with reasons, are kept in `Briefing.removed_claims`. Problems in the headline or the structure can't be pruned.
- **Retry.** If no candidate passes or can be repaired, the verifier sends the reasons for the closest candidate back to the tactician. That happens at most twice, and only if another round fits the time budget (`SECOND_ACT_HALFTIME_BUDGET_S`, default 25 s). The budget check is predictive: elapsed time plus the last round's duration.
- **Fallback.** After the retries, or when time runs out, `template_briefing` builds the briefing directly from the signals. It's grounded by construction, and its own check result is logged too.

## Briefing cache

`orchestration/cache.py` stores verified online briefings in `.cache/briefings/` (gitignored, set with `SECOND_ACT_CACHE_DIR`). The key is built from:

- every event seen so far;
- the model deployment and reasoning effort;
- `PROMPT_VERSION`, which must be bumped whenever prompts or verifier rules change.

Replaying the same match reuses the briefing without calling the model: 0.8 s instead of about 22 s. Offline briefings are deterministic and aren't cached.

## Logging

`orchestration/agent_log.py` writes one JSONL file per session to `logs/` (gitignored). It records:

- each draft (attempt, candidate, latency, tokens, output) and each support check;
- each verdict (layer and issues), retries and fallbacks;
- the workflow total.

`AgentLog.summary()` rolls these up into grounding, retry, latency and token totals.

## Measured so far (demo match m-001, online, gpt-5-mini)

| Build | Runs | Outcome | Whistle to briefing |
| --- | --- | --- | --- |
| One draft per round, before signal labels | 5 | 3 verified first time, 1 verified after a retry (32 s), 1 fallback | 20–32 s |
| Two parallel drafts, before signal labels | 4 | 3 verified, 1 fallback (both drafts misread Kestrel's PPDA) | 21–25 s |
| Two parallel drafts, with labels | 4 | 4 verified; 1 of 8 drafts rejected by the model check | 22–26 s |
| Plus the thin-baseline rule and pruning (current) | 6 | 6 verified, 0 fallbacks; 2 of 12 drafts rejected, each covered by the other candidate | 20–23 s |

Each current briefing uses about 14k input and 4.6k output tokens across two drafts and two support checks.

What the verifier has caught on real drafts:

- invented IDs, such as a window range written as one ID;
- misread directions, such as reading Kestrel's PPDA as Harbour's press;
- a claim that mentioned possession without citing it.

## Running it

```bash
PYTHONPATH=src .venv/bin/python -m second_act.orchestration.cli m-001             # online
PYTHONPATH=src .venv/bin/python -m second_act.orchestration.cli m-001 --offline   # template, no model
```

## Watcher (second half)

A prediction compares the mean of its metric over the completed second-half windows with the first-half window mean. The watcher cites both values as signals, for example `sig-channel_entries_wmean-HAR-left-p1` and `...-p2`. It judges a prediction only after that prediction's `evaluate_after_minute`, and only once at least 3 second-half windows are complete.

- **Confirmed:** the change reaches the threshold in the predicted direction.
- **Contradicted:** the change is under half the threshold, or goes the other way.
- **Open:** anything in between. At full time every open prediction gets a final verdict.
- **Reversals:** a decided prediction flips only when the opposite rule clearly holds. In the demo, "Harbour's press fades" is contradicted at 65' and confirmed at 80'.

On each status change, online, the model rephrases the template sentence. It must keep the same numbers (deterministic check) and must not use words that contradict the verdict (keyword guard). Otherwise the template sentence is used. At full time the watcher re-evaluates every prediction, so the recap quotes final values.

## Analyst (live triggers and player tags)

At each window close the analyst compares the window with a baseline. The baseline is the first-half average during the second half, and the half average so far during the first half. It raises at most one trigger per 5 match minutes:

- **Channel surge:** at least 3 entries and at least twice the baseline.
- **Press burst:** high regains well above the baseline.
- **Turnover height jump:** 12 m or more.
- **Territory:** at least 80% field tilt.
- **Chances:** at least 0.4 xG in the window.

Player tags (at most one per 10 minutes, each player at most once per kind):

- shots above 100 km/h;
- top speeds of 34 km/h or more;
- 4 or more difficult passes completed.

## Presenter (variants)

Every source item becomes 12 cues: analyst or casual, English or Spanish, and three club-focus settings (none, home, away). They're generated when the item is created, so switching on the frontend is instant.

- **Template content** (live triggers, tags, template briefings and recaps, watcher verdict sentences) is rendered from `i18n/catalog.py`. Spanish uses decimal commas.
- **Model-written briefings and recaps** are rewritten by the model, one call per variant, in parallel. Every rewritten line must pass the number check, which reads decimal commas in Spanish. A line that fails falls back to the English analyst line.
- **Prediction checks** use the model's sentence for the English analyst line, and the catalogue (which states every verdict correctly) for the other variants.
- **Club focus:**
  - live cues get favourable or worrying framing ("Good news for Kestrel fans:" / "Alarma en Kestrel:");
  - briefings and recaps put the club's read first and prefix the headline.

## Match runner and API

`orchestration/match_runner.py` replays a stored match, emitting:

- `match`, `event`, `score` and `status` as play unfolds;
- `cue`, `predictions`, `briefing` and `recap` as the agents produce them;
- `end` when the replay finishes.

At each window close it refreshes the signals and runs the analyst. In the second half it also runs the watcher, as an ordered background task, so model calls never stall the stream.

At half-time the stream pauses. The English analyst briefing is sent first (`status: partial`), then all variants (`status: ready`). At full time come the final verdicts, the recap and its variants.

`api/app.py` (FastAPI) serves the endpoints in PRD section 10. `/api/sessions/{id}/stream` is server-sent events. Each session keeps its history, so late or reconnecting clients replay from the start.

## Measured: full online replay of m-001 (gpt-5-mini, nothing cached)

| Stage | Result |
| --- | --- |
| Half-time briefing | Model-written, verified on the first round (23 s). English analyst variant streamed at 23.6 s, all 12 variants by 33.9 s |
| Prediction checks | 6 predictions; status changes explained by the model (2–3 s each), every sentence passing the number check and the verdict guard |
| Full-time recap | Model-written, 1 rejected draft (an unsupported claim), verified in 27 s; all variants 12 s later |
| Whole match | 21 model calls, about 35k input and 16k output tokens |

An offline replay of the whole match takes about 2.5 s and produces about 2,900 messages, 300 of them cues.
