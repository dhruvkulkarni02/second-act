# Submission text

Challenge: **The Synthetic Match Insights Engine for Premier League Studio** (Microsoft × Premier League *Inside the Game* hackathon).

## Project fields

- **Name:** Second Act
- **Tagline:** A half-time tactical read that checks itself.
- **Keywords:** multi-agent, Microsoft Agent Framework, Microsoft Foundry, grounding, verification, synthetic data, football analytics, broadcast graphics, Azure Container Apps, explainable AI
- **Live demo:** https://second-act.bluecliff-fc92b3c6.swedencentral.azurecontainerapps.io
- **Repository:** https://github.com/dhruvkulkarni02/second-act
- **Video:** https://youtu.be/4EDuElALqFs

## Short description (about 50 words)

Second Act turns a live stream of synthetic match events into explainable studio graphics. At half-time it explains each team's tactics and predicts each manager's changes as measurable tests. A verifier checks every number against computed signals. In the second half each prediction is confirmed or contradicted on screen, and a verified recap closes the match.

## Pitch

**What we built.** A multi-agent system for the studio and streaming feed. A seeded generator produces realistic synthetic matches whose tactics come from configuration: pressing height, channel preference, a half-time switch to a back five. An analyst agent computes over a thousand tactical signals per match, each with a stable, citable ID: PPDA, field tilt, channel entries, turnover height, xG, pass difficulty, a control-vs-chaos index, and sprints from 1 Hz tracking. It raises live cues as play unfolds. At the half-time whistle a tactician agent writes the briefing: what's working, what isn't, and two or three predicted adjustments per team, each a test the system can check. A verifier rejects any draft whose numbers can't be traced to a cited signal, uses a second model call to judge whether each claim follows from its evidence, and removes, redrafts or falls back to a grounded template. A watcher then confirms or contradicts each prediction from the second-half data. A presenter delivers everything as timed overlay cues, in analyst and casual versions, in English and Spanish, and from either club's point of view.

**Microsoft and Azure technologies.**
- **Microsoft Agent Framework (Python):** the agents and a `WorkflowBuilder` graph (writer → verifier → retry or fallback), shared by the half-time briefing and the full-time recap, with parallel drafts and a time budget.
- **Microsoft Foundry:** a `gpt-5-mini` deployment through `FoundryChatClient` with strict structured outputs, used by the tactician, the verifier's support check, the watcher and the presenter. **Foundry evaluations** (the built-in groundedness evaluator) measure grounding before and after the verifier.
- **Azure Container Apps:** hosts the FastAPI backend and web app, with a system-assigned **managed identity** for both Foundry and Azure Container Registry, so there are no keys anywhere. Logs go to **Azure Monitor / Log Analytics**.
- **GitHub Actions:** tests, lint and the container build on every push.

**What it does.** It replays a match as a live stream at 30× speed. During the first half it raises live cues about overloads, pressing bursts and fast shots, each showing its evidence. At half-time the stream pauses for a verified briefing; the English analyst version is streamed within about 25 seconds of the whistle. In the second half a predictions panel turns each test green (confirmed) or red (contradicted), with a grounded one-line explanation. At full time it produces a recap of how the tactical battle played out, checked against the watcher's verdicts. Every cue follows a documented JSON contract that a broadcast graphics system could consume directly.

**The problem it solves.** Studio analysis makes confident tactical calls at half-time and never comes back to them. Automated commentary from language models invents numbers. Second Act makes the read accountable: every number traces to the data, every prediction is a test, and the system shows on air when it was wrong.

## Results to quote

The grounding, latency and token figures are in the README's "Results" section and in `docs/evaluation/grounding.json`.
