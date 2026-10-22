# Second Act: Product Requirements Document

**Tagline:** A half-time tactical read that checks itself.
**Hackathon:** Microsoft × Premier League "Inside the Game: Developer Hackathon"
**Challenge:** The Synthetic Match Insights Engine for Premier League Studio
**Builder:** Dhruv (solo)
**Submission deadline:** Tuesday, October 27, 2026, 11:59 PM Pacific (target: submit Tuesday evening Eastern)
**Official rules:** https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md

---

## 1. Instructions for Claude Code

Build this project milestone by milestone in the order given in section 12. Commit after each working step. Before writing any Microsoft Foundry or Microsoft Agent Framework code, check the current SDK packages and APIs on Microsoft Learn (the Microsoft Learn MCP server at `https://learn.microsoft.com/api/mcp` can be added for this), because these products change quickly and remembered APIs may be out of date. If a requirement here is unworkable or would cost more than the schedule allows, say so and propose an alternative instead of silently changing it. At the end of each milestone, summarize what's done, what's at risk, and whether to apply the cut order in section 13.

The repo will be public. Never commit secrets: all keys and endpoints come from environment variables, with a `.env.example` committed and `.env` gitignored.

---

## 2. Product summary

Second Act is a multi-agent system that turns a live stream of synthetic football events into explainable match intelligence for studio and streaming, built around a half-time tactical briefing that holds itself accountable.

During the first half, it tracks tactical signals and surfaces a few live cues. At half-time, it explains what's working for each team and predicts what each manager will change, with every prediction written as a measurable test. A verifier checks every claim against the data. In the second half, it confirms or rejects each prediction on screen as play unfolds. At full time, it produces an automated recap of how the tactical battle played out. Everything renders as timed overlay cues, personalized by viewer mode, favourite club, and language.

The differentiator is accountability: most entries explain what happened, while Second Act makes a tactical read and checks itself against the data.

---

## 3. Goals and success criteria

The project must clearly cover the challenge's five pipeline stages (ingest, interpret, explain, render, personalize) and score well on the five equally weighted judging criteria: technological implementation (including synthetic data creation and code quality), agentic design, real-world impact, user experience and presentation, and category adherence. The target prizes are the Grand Prize and Best Multi-Agent System.

The project is successful when:

- A full synthetic match replays end to end in a deployed web app at a public URL.
- The half-time briefing contains no number that isn't traceable to a computed signal.
- At least one prediction is shown confirmed and at least one shown not happening in the demo match.
- The same match produces visibly different output in analyst mode and casual mode, and in English and Spanish.
- The full-time recap is generated automatically and passes verification.
- Tests pass in CI in offline mode (no model calls).

---

## 4. Users

**Broadcast and studio producers** need on-screen insight that is timed, machine-readable, and safe to air without invented numbers.

**Analysts** want the numbers: pressing metrics, pass networks, channel breakdowns, evidence for every claim.

**Casual fans** want a short plain-language story of what's happening tactically and why it matters to their club.

---

## 5. Scope

**In scope:** synthetic match generator; analytics engine; five agents with shared state; half-time briefing; second-half prediction checks; full-time recap; first-half live cues; player tags for speed and pass quality; overlay cue stream; web frontend; analyst and casual modes; favourite-club framing; English and Spanish; deployment to Azure Container Apps; CI; demo video and README.

**Out of scope:** real match data, real clubs, players, logos, or kits; auto-eventing from video; user accounts or authentication; mobile apps; any persistent database beyond what the demo needs.

---

## 6. Synthetic data

### 6.1 Generator requirements

- Two fictional clubs, **Harbour Town (HAR)** and **Kestrel Athletic (KES)**, each with a full fictional squad (starting XI plus bench) with names, shirt numbers, and positions.
- Pitch coordinates in metres: x from 0 to 105, y from 0 to 68.
- Seeded and deterministic: the same seed and config produce the same match.
- Tactical behaviour driven by config parameters, not hard-coded events. Parameters per team per phase include pressing height, press intensity, preferred attacking channel, defensive line depth, team width, formation, and tempo. Parameters can change at half-time and after substitutions.
- Output: a JSONL event file per match, plus optional player position snapshots at roughly 1 Hz (used for speed, distance, defensive line height, and team width). If snapshots slip, derive what's possible from event locations and record the limitation in the README.
- Generate the demo match plus at least two differently seeded matches to show that the analytics detect patterns rather than reading the script.
- Commit the generator, its configs, and the generated sample datasets, with documentation.

### 6.2 Demo match story

The demo match config must produce this story through its parameters:

- **First half:** Harbour press high and overload the left channel. Kestrel repeatedly lose the ball in their own third. Harbour lead 1–0.
- **Half-time:** Kestrel's manager makes two changes, for example switching to a back five and dropping a midfielder deeper.
- **Second half:** one change clearly works (Harbour's left-channel entries drop and Kestrel equalize); the other has little measurable effect.

### 6.3 Event schema

Define with Pydantic and validate every generated file.

```json
{
  "event_id": "e-000412",
  "match_id": "m-001",
  "period": 1,
  "minute": 23,
  "second": 41,
  "type": "pass",
  "team": "HAR",
  "player_id": "HAR-11",
  "x": 38.2, "y": 12.5,
  "end_x": 61.0, "end_y": 9.8,
  "outcome": "complete",
  "under_pressure": true,
  "pressure_player_id": "KES-4",
  "speed_kmh": 54.0,
  "body_part": "foot"
}
```

Event types: `pass`, `carry`, `shot`, `tackle`, `interception`, `pressure`, `clearance`, `foul`, `card`, `substitution`, `possession_change`, `goal`, `period_start`, `period_end`. Match metadata (teams, squads, formations, kickoff, venue name) lives in a separate metadata file.

Position snapshot schema: `match_id`, `period`, `t` (seconds), and a list of `{player_id, x, y}` plus the ball position.

---

## 7. Analytics

All analytics are plain, deterministic, tested Python with no model calls.

### 7.1 Frames and windows

Normalize coordinates so each team always attacks toward x = 105. Thirds are x 0–35, 35–70, 70–105. Channels are left (y 0–22.67), centre (22.67–45.33), and right (45.33–68), relative to the attacking team. Compute signals in fixed five-minute windows per half and cumulatively per half.

### 7.2 Signals

| Signal | Definition |
| --- | --- |
| Possession share | Share of on-ball events per team, per window and per zone |
| Field tilt | Team's share of all final-third passes |
| PPDA | Opponent passes in their own defensive 60% of the pitch divided by the team's defensive actions (tackles, interceptions, fouls) in that area |
| Turnover height | Average x where the team wins the ball |
| Channel entries | Passes and carries entering the final third, per channel, per window |
| Pass network | Average player positions and pass counts between pairs, per half |
| Progressive passes | Completed passes that move the ball at least 10 m closer to the opponent's goal |
| Pass difficulty | 0–100 rating from distance, pressure, forwardness, and whether the pass bypasses opponents; document the formula |
| Shot quality (xG) | Simple model from distance, angle, and body part; document it |
| Rhythm | Average possession sequence length and turnovers per minute |
| Control vs chaos index | Combination of sequence length and turnover rate; document the formula |
| Defensive line height and width | From position snapshots, per window |
| Speed tags | Sprints above 25 km/h and shots above 100 km/h, from snapshots and event speed |

### 7.3 Signal IDs

Every computed value is stored in the shared state with a stable, readable ID, for example `sig-channel_entries-HAR-left-p1w4`, along with its value, unit, team, scope, and window. Agents cite these IDs, and the verifier checks them.

---

## 8. Agents and orchestration

Use Microsoft Agent Framework for orchestration and a model hosted in Microsoft Foundry. The model deployment name and endpoint are configurable. Every agent reads from and writes to one shared match state. Every model-backed agent must also have an **offline mode** that produces deterministic template output, used in tests, CI, and as a fallback.

### 8.1 Analyst

Consumes events as they arrive, updates signals in the shared state, and raises **live triggers** when a signal crosses a threshold (for example, one channel's entries doubling against the half average, or turnover height jumping). Rate-limit triggers to at most one cue per five minutes of match time. Mostly code; an optional model step may rank which triggers are worth showing.

### 8.2 Tactician

At half-time, reads first-half state and writes the briefing as structured JSON:

- for each team: what's working, what isn't, each point with evidence signal IDs
- two or three predicted adjustments per team, each with a measurable test (8.6)
- a one-line headline for the half

At full time, writes the recap: the first-half story, what changed at the break, which predictions held, and how the result came about, again citing evidence.

### 8.3 Verifier

Checks every briefing and recap before it reaches the presenter. Two layers:

1. **Deterministic:** every cited signal ID exists; every number in the text matches a cited signal's value within a small tolerance; every prediction test is well-formed and evaluable.
2. **Model check:** each claim is actually supported by its cited evidence.

On failure, returns specific reasons to the tactician for a retry. After two failed retries, falls back to a template briefing built directly from the signals. Record every rejection and retry in the logs, since this is the failure-recovery story for the multi-agent judges.

### 8.4 Watcher

During the second half, evaluates each prediction test deterministically after its `evaluate_after_minute`, setting status to `confirmed`, `contradicted`, or `open`. When status changes, it asks the model for a one-sentence explanation grounded in the before-and-after values, which also passes the deterministic verifier check.

### 8.5 Presenter

Turns live triggers, the briefing, prediction checks, player tags, and the recap into overlay cues (section 9). Produces variants for each audience (analyst, casual) and language (English, Spanish), and applies favourite-club framing when a club is set (the same facts told from that club's point of view). Generate the variants when the source content is created, so the frontend can switch instantly.

### 8.6 Prediction test schema

```json
{
  "prediction_id": "p-KES-2",
  "team": "KES",
  "claim": "Kestrel switch to a back five, reducing Harbour's left-side attacks",
  "metric": "channel_entries",
  "subject_team": "HAR",
  "scope": {"channel": "left"},
  "comparison": "second_half_window_mean_vs_first_half_mean",
  "direction": "decrease",
  "threshold_pct": 30,
  "evaluate_after_minute": 55,
  "status": "open"
}
```

### 8.7 Logging

Log each agent's inputs, outputs, retries, latency, and token use as JSONL per session. Summarize grounding results (unsupported claims caught, retries, fallbacks), latency, and tokens in the README.

---

## 9. Overlay cue contract

```json
{
  "cue_id": "c-0031",
  "match_time": "52:10",
  "duration_s": 8,
  "kind": "prediction_check",
  "audience": "casual",
  "language": "en",
  "club_focus": "KES",
  "headline": "The switch is working",
  "body": "Kestrel's back five has cut Harbour's left-side attacks by half since the break.",
  "evidence": ["sig-channel_entries-HAR-left-p2w2", "sig-channel_entries-HAR-left-p1"],
  "prediction_id": "p-KES-2",
  "status": "confirmed"
}
```

Cue kinds: `live_trigger`, `player_tag`, `halftime_briefing`, `prediction_check`, `fulltime_recap`. Define the contract as a Pydantic model and document it in the README as the integration point a broadcast graphics system would consume.

---

## 10. Backend API

FastAPI. The backend replays a stored synthetic match as a live stream at configurable speed (default 30x, so a half takes about three minutes), pausing at half-time while the briefing is generated.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Health check |
| GET | `/api/matches` | List available synthetic matches |
| POST | `/api/sessions` | Start a session: `match_id`, `speed`, `offline` flag |
| GET | `/api/sessions/{id}/stream` | Server-sent events: match events, score and clock, and cues for all variants |
| GET | `/api/sessions/{id}/briefing` | Half-time briefing (all variants) |
| GET | `/api/sessions/{id}/recap` | Full-time recap (all variants) |
| GET | `/api/sessions/{id}/predictions` | Prediction statuses |

Target: the half-time briefing, including verification and all variants, is ready within 30 seconds of the half-time whistle. If it isn't, the frontend shows a "briefing in progress" state rather than freezing.

---

## 11. Frontend

A single web app served by the backend, built with plain HTML, CSS, and JavaScript (or a lightweight framework if it clearly saves time). No external images.

- A 2D pitch (SVG or canvas) showing ball and event locations, with the score, clock, and team names in a broadcast-style header.
- Overlay cards in a lower-third style for live triggers, player tags, and prediction checks, with confirmed and contradicted states visually distinct.
- A half-time screen presenting the briefing, with evidence expandable in analyst mode.
- A full-time recap screen.
- Controls: analyst or casual mode, English or Spanish, favourite club (none, HAR, KES), and replay speed. Switching mode or language updates the display immediately.
- A panel listing predictions and their live status.
- Clean, broadcast-graphics look that reads well in a screen recording at 1920 × 1080.

---

## 12. Milestones

**M1, Thursday Oct 22: setup and data.** Repo scaffold (section 15), Azure and Foundry configured with one successful model call from code, event and snapshot schemas, and the generator producing the demo match plus two other seeds.
*Done when* all three matches validate against the schema and the demo match ends 1–1 with the planted story present in the raw events.

**M2, Friday Oct 23: analytics.** All signals in section 7 with unit tests and signal IDs.
*Done when* a printed signal summary of the demo match shows Harbour's first-half left-channel overload, Kestrel's low turnover height, and the second-half drop in left-channel entries.

**M3, Saturday Oct 24: briefing and verification.** Shared state, tactician, verifier with retry and fallback, and the orchestration workflow up to half-time, in both online and offline modes.
*Done when* the demo match produces a verified briefing with evidence citations and measurable predictions, and a test proves the verifier rejects an invented number.

**M4, Sunday Oct 25: second half and output.** Watcher, live triggers, player tags, full-time recap, presenter with all variants, and the full SSE stream.
*Done when* a full replay produces the complete cue stream, with at least one prediction confirmed and one contradicted, and a verified recap.

**M5, Monday Oct 26: frontend and deployment.** The web app, a Dockerfile, deployment to Azure Container Apps, and CI.
*Done when* the public URL runs the full demo match in a browser and CI passes.

**M6, Tuesday Oct 27: submission.** README, architecture diagram, demo video, pitch text, final checks, submission.

---

## 13. Cut order

If behind schedule, cut in this order:

1. Reduce first-half live cues to the minimum.
2. Drop favourite-club framing.
3. Drop position snapshots and their derived signals (defensive line, sprints), keeping event-based speed tags.

Never cut: the half-time briefing, the verifier, the second-half prediction checks, the full-time recap, Spanish output, the deployed demo, or the video.

If ahead of schedule, add in this order: Foundry evaluation runs measuring grounding before and after the verifier; Azure MCP so agents query match state through tools; a player-focus mode that follows one player through the match.

---

## 14. Non-functional requirements

- Python 3.11+, type hints throughout, Pydantic models for all data contracts, Ruff for linting, pytest for tests.
- Tests cover the schema, generator determinism, every analytics signal, the verifier's deterministic checks, prediction evaluation, and an offline end-to-end run.
- CI (GitHub Actions) runs lint and tests in offline mode on every push.
- All configuration via environment variables; `.env.example` committed; no secrets in the repo or logs.
- The deployed demo must stay available, free and without login, until November 10, 2026.
- Clear docstrings on public functions and a module-level docstring for each package.

---

## 15. Repo structure

```
second-act/
  README.md
  PRD.md
  pyproject.toml
  .env.example
  .gitignore
  Dockerfile
  .github/workflows/ci.yml
  src/second_act/
    schema/          # Pydantic models: events, snapshots, metadata, signals, predictions, cues
    sim/             # generator, team and phase configs, demo story config
    analytics/       # frames, windows, signals, xG, pass difficulty
    state/           # shared match state
    agents/          # analyst, tactician, verifier, watcher, presenter
    orchestration/   # Agent Framework workflow, offline mode
    api/             # FastAPI app, SSE replay
    i18n/            # language handling
  web/               # frontend
  data/              # generated sample matches and metadata
  logs/              # gitignored session logs
  tests/
  docs/              # architecture diagram, cue contract, formulas
```

---

## 16. Submission deliverables

- **Public GitHub repo** with the README covering: what it is and why it matters; an architecture diagram mapping the five pipeline stages to components and agents; the Microsoft technologies used and where; how the generator works; how to run locally (online and offline); the deployed link; grounding, latency, and token results; and known limitations.
- **Deployed demo URL** on Azure Container Apps.
- **Demo video under two minutes**, public on YouTube or Vimeo, screen recording with voiceover and no copyrighted music or third-party trademarks. Structure: 0:00–0:15 the problem; 0:15–0:40 first half with live cues; 0:40–1:10 half-time briefing, toggling analyst and casual, then Spanish; 1:10–1:40 second half with one prediction confirmed and one contradicted, then the recap; 1:40–2:00 architecture and Microsoft technologies.
- **Pitch text:** what was built, which Microsoft and Azure technologies it uses, what it does, and what problem it solves.
- **Project fields:** name "Second Act", tagline, keywords, and description, linked to the challenge "The Synthetic Match Insights Engine for Premier League Studio."
