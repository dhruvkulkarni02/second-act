# Agents and orchestration

M3 covers the half-time path. The watcher, the live triggers and the presenter arrive in M4.

## Shared match state

`state.MatchState` is the single state object every agent reads and writes:

- **Events and snapshots** seen so far, plus the match clock.
- **The `SignalSet`** computed from them by `refresh_signals()`, using the same `compute_signals` the analytics tests cover.
- **The verified `Briefing` and its `Prediction`s.** The watcher will update their statuses in M4.
- **Cues** for the presenter, from M4 on.

An agent can only cite a signal that exists in `state.signals`. At half-time the state holds only first-half data, so a second-half ID like `sig-xg-HAR-p2` is rejected as unknown.

## Half-time workflow (Microsoft Agent Framework)

`orchestration/halftime.py` builds a `WorkflowBuilder` graph with three executors:

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
    - every prediction is evaluable: allowed metric, valid scope, first-half window baseline, threshold 5–80%, evaluated after minute 60–85, ID format;
    - structure: two teams, 1–3 points per section, 2–3 predictions each.
  - **Layer 2 (online only):** one batched model call judges whether each claim's meaning follows from its cited values. It sees the metric definitions with the evidence. A failure in layer 2 itself is logged but doesn't block a draft that already passed layer 1.
- **Retry.** If no candidate passes, the verifier sends the reasons for the closest candidate back to the tactician. That happens at most twice, and only if another round fits the time budget (`SECOND_ACT_HALFTIME_BUDGET_S`, default 25 s). The budget check is predictive: elapsed time plus the last round's duration.
- **Fallback.** After the retries, or when time runs out, `template_briefing` builds the briefing directly from the signals. It's grounded by construction, and its own check result is logged too.

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
| Two parallel drafts, with labels (current) | 4 | 4 verified; 1 of 8 drafts rejected by the model check | 22–26 s |

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
