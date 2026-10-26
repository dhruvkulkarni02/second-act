# Grounding evaluation (Microsoft Foundry)

`scripts/evaluate_grounding.py` runs the half-time workflow online three times on each synthetic match (9 runs, nothing cached, `gpt-5-mini` with reasoning effort `minimal`). It then scores every claim, meaning each headline and each working or not-working point:

- **before** the verifier: every claim in the tactician's raw drafts (two per run);
- **after**: every claim in the briefing the verifier lets through.

Each claim is scored two ways:

1. **Deterministic:** untraceable numbers, unknown signal IDs, missing evidence. This is the verifier's own first layer, so the "after" count must be zero.
2. **Foundry groundedness:** the built-in `groundedness` evaluator, run by Microsoft Foundry as an independent LLM judge, separate from the verifier's own support check. It sees only the values the claim cites, with what each metric means, and scores the claim from 1 to 5.

Results are in `evaluation/grounding.json` and per claim (text, cited context, score and the judge's reason) in `evaluation/claims.jsonl`. Each Foundry run also has a link to the Foundry portal.

## Results (current verifier)

| | Claims | Claims with untraceable numbers | Foundry groundedness pass rate | Mean score |
| --- | --- | --- | --- | --- |
| Raw drafts (before) | 187 | 12 (9 untraceable numbers, 4 other issues) | 138 / 187 = **74%** | 4.04 |
| Verified briefings (after) | 89 | **0** | 73 / 89 = **82%** | 4.29 |

- **All 9 runs** produced a model-written briefing, with no template fallbacks. The verifier removed 0–3 claims per run (median 2).
- **Whistle to verified briefing:** 20–34 s (median 22 s), at about 16k input and 4.5k output tokens per run. One run exceeded the 30-second target.

## How the evaluation changed the verifier

The first measurement found no improvement. That was the point of running an independent judge:

| Verifier version | Verified claims passing Foundry's judge | Template fallbacks |
| --- | --- | --- |
| v5 (number check plus a lenient support check) | 60 / 85 = 71% (raw drafts: 124 / 177 = 70%) | 0 of 9 |
| Stricter support check, first attempt | 69 / 72 = 96%, but almost all of it was the template | **8 of 9** |
| Stricter checks, repair before the support check, headline replacement | 79 / 95 = 83% (raw drafts: 75%) | 0 of 9 |
| Current (v9): plus guards for recurring misreadings and category checks | 73 / 89 = **82%** (raw drafts: 74%) | 0 of 9 |

The v5 results are archived in `evaluation/v5-verifier/`.

The judge's reasons for the claims v5 let through fell into three groups. Each one led to a change:

1. **One-sided comparisons.** "Their press is light compared with Kestrel's" cited only one team's PPDA.
   → A deterministic rule: a comparison that names a team must cite that team's signal. A comparison within one team ("24 on the left versus 4 on the right") is fine.
2. **Causes and forecasts the values don't show.** "A clear avenue to exploit", "has led to goals", "risks tiring".
   → The support check now rejects them, and the tactician is told to describe, not speculate.
3. **Misreadings of position.** For example, "won the ball very high in the opponent half" with a turnover height of 47.3 m, short of the 52.5 m halfway line.
   → Metric notes now give the halfway line, and the support check is told to look for misreadings like this.

Reviewing the regenerated demo briefings then found three recurring problems the numbers alone didn't show. Each became a rule:

- **PPDA read backwards.** "Kestrel are being pressed less (PPDA 11.2)": PPDA measures a team's own pressing. → A deterministic guard on passive "pressed" phrasing with a PPDA signal.
- **A team's own high regains read as it losing the ball.** → A deterministic guard: a point filed under a team, citing that team's own regains, that talks about losing the ball.
- **Points under the wrong heading.** Being pinned back listed as "working". → The support check now judges the category too.

The current results above include these. Signal IDs that the model sometimes wrote into the prose are now stripped as formatting.

The stricter checks first rejected nearly every draft. Two structural problems caused that:

- **Repair needed a second round.** A draft with one failing claim needed a sequential second model call, which didn't fit the time budget. The verifier now prunes deterministic failures *before* its single, parallel support check.
- **Headlines couldn't be repaired.** The model's one-line summaries were often interpretive, and a headline can't be pruned. A rejected headline is now replaced with the template headline, which is grounded by construction, and the model's verified points are kept.

## What's still not caught

The 16 verified claims Foundry's judge still fails are mostly **implicit comparisons**: "created substantially more chances" citing only one team's xG, without naming the other team. The comparison rule only fires when the other team is named. A few are borderline wording the two judges disagree on. Model-written rewrites into other audiences and languages are number-checked but not meaning-checked (see the README's known limitations).

## Reproduce

```bash
PYTHONPATH=src python scripts/evaluate_grounding.py --runs 3          # regenerate and evaluate (about 10 min)
PYTHONPATH=src python scripts/evaluate_grounding.py --reuse           # re-run only the Foundry evaluation on saved claims
```
