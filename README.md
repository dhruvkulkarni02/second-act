# Second Act

**A half-time tactical read that checks itself.**

Second Act is a multi-agent system that turns a live stream of synthetic football events into explainable match intelligence. Its centrepiece is a half-time briefing that predicts each manager's adjustments as measurable tests, then confirms or rejects them on screen during the second half. Built for the Microsoft × Premier League *Inside the Game* hackathon. See [PRD.md](PRD.md).

> Status: milestone M1 (scaffold, schemas, synthetic data, Foundry connectivity). The full README arrives in M6.

## Quick start (offline)

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
second-act-generate generate        # regenerate data/ (deterministic)
second-act-generate story           # check the demo story in the raw events
pytest                              # offline: no model calls
```

## Microsoft Foundry (online mode)

1. Create a Foundry project with a model deployment.
2. Copy `.env.example` to `.env` and set `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL`.
3. Run `az login`. Auth goes through Microsoft Entra ID (`DefaultAzureCredential`), so the app stores no keys.
4. `python scripts/foundry_smoke.py` makes one call through Microsoft Agent Framework (`agent-framework-foundry`, `FoundryChatClient`) and prints the latency and token counts.

## Synthetic data

Three seeded matches between two fictional clubs, Harbour Town and Kestrel Athletic, live in `data/`. Each has events, metadata and 1 Hz position snapshots. [docs/generator.md](docs/generator.md) covers how tactics parameters become events, how the demo story is planted and checked, and the known limitations.
