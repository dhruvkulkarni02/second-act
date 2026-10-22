# Second Act

**A half-time tactical read that checks itself.**

Second Act is a multi-agent system that turns a live stream of synthetic football events into explainable match intelligence. Its centrepiece is a half-time briefing that predicts each manager's adjustments as measurable tests, then confirms or rejects them on screen during the second half. Built for the Microsoft × Premier League *Inside the Game* hackathon. See [PRD.md](PRD.md).

> Status: M1 (scaffold, schemas, synthetic data, Foundry connectivity), M2 (analytics) and M3 (half-time briefing with verification) are done. The full README arrives in M6.

## Quick start (offline)

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
second-act-generate generate        # regenerate data/ (deterministic)
second-act-generate story           # check the demo story in the raw events
second-act-signals m-001            # print the computed signal summary
pytest                              # offline: no model calls
```

## Microsoft Foundry (online mode)

1. Create a Foundry project with a model deployment.
2. Copy `.env.example` to `.env` and set `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL`.
3. Run `az login`. Auth goes through Microsoft Entra ID (`DefaultAzureCredential`), so the app stores no keys.
4. `python scripts/foundry_smoke.py` makes one call through Microsoft Agent Framework (`agent-framework-foundry`, `FoundryChatClient`) and prints the latency and token counts.

The M1 connectivity check ran against a `gpt-5-mini` Global Standard deployment in Sweden Central: each round trip took 3–6 s, with reasoning effort `low` (`FOUNDRY_REASONING_EFFORT`).

## Synthetic data

Three seeded matches between two fictional clubs, Harbour Town and Kestrel Athletic, live in `data/`. Each has events, metadata and 1 Hz position snapshots. [docs/generator.md](docs/generator.md) covers how tactics parameters become events, how the demo story is planted and checked, and the known limitations.

## Analytics

`second_act.analytics.compute_signals` computes every signal in PRD section 7, each with a stable ID such as `sig-channel_entries-HAR-left-p1w4`, per five-minute window and per half. It uses no model calls. Definitions and formulas, including xG, pass difficulty and the control index, are in [docs/formulas.md](docs/formulas.md).

## Half-time briefing

At the half-time whistle a Microsoft Agent Framework workflow runs. The tactician, on a Foundry model, drafts the briefing: what's working for each team, plus predicted adjustments written as measurable tests. The verifier rejects any draft whose numbers can't be traced to a cited signal or whose claims its evidence doesn't support. The verifier removes individual unsupported claims where it can. Otherwise it sends the draft back for a retry, or falls back to a template built directly from the signals. Verified briefings are cached per match and model, so replays don't call the model again. See [docs/agents.md](docs/agents.md).

```bash
second-act-halftime m-001            # online (Foundry)
second-act-halftime m-001 --offline  # no model calls
```
