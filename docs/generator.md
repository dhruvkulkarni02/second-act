# Synthetic match generator

Everything in `data/` comes from `src/second_act/sim`. Nothing comes from real matches, clubs or players.

```bash
second-act-generate generate             # all configs -> data/<match_id>/
second-act-generate story                # check the demo story in data/m-001
second-act-generate find-seed m-001      # search for a seed that tells the demo story
```

## Outputs per match

| File | Contents |
| --- | --- |
| `events.jsonl` | One `Event` per line (`schema/events.py`), in time order |
| `metadata.json` | Teams, squads, starting formations, venue, kickoff, seed, period lengths, attacking directions |
| `snapshots.jsonl.gz` | One `Snapshot` per second: all 22 on-pitch players and the ball |

Coordinates are metres on a 105 × 68 pitch in the raw frame. The home team attacks toward x = 105 in period 1 and the away team does so in period 2. To move into a team's frame, rotate the pitch 180° (`analytics/frames.py`). Rotating rather than mirroring keeps "left" on the attacking team's left: in a team frame, left is y 0–22.67.

Metadata only carries what a broadcaster knows before kickoff: lineups and starting formations. Tactical parameters and planned changes stay in the config, so agents can't read the script.

### Schema extensions beyond the PRD example

- `sequence_id`: possession sequence number, which increments on every change of possession.
- `related_player_id`: the pass receiver, the player fouled, or the player coming on for a substitution.
- `t`: seconds since the period started (the float behind `minute`/`second`).
- `possession_change.outcome` is `open_play` (ball won) or `dead_ball` (restart awarded).

## How tactics become events

The engine (`sim/engine.py`) is a possession-by-possession Markov model with one seeded `random.Random`. For each on-ball action:

1. **Pressure.** The defending team presses with probability `press_intensity × sigmoid((press_line − x) / 7)`, where `press_line = 25 + 55 × press_height` in the attacker's frame. Near the box defenders always close down. A pressure produces a `pressure` event and flags the next action `under_pressure`. Some pressures become fouls, and some fouls become cards.
2. **Action choice.** Shot probability depends on zone (box, edge of box, final third). Carry probability depends on `tempo` and on whether the player is under pressure. Everything else is a pass.
3. **Passes.** Length and directness scale with `tempo`. In the middle third, lateral drift pulls toward the preferred channel. A pass that enters the final third samples its channel from `channel_weights`. Completion is `1.03 − 0.0055 × distance`, reduced by pressure (offset by `buildup_security`), by passes into the box, by long balls, and for final-third entries by the defender's `wide_block`/`central_block`. **A back five adds +0.45 wide block**, which is how Kestrel's half-time switch cuts Harbour's left-channel entries. A failed pass ends as an interception somewhere along its line, goes out of play, or is cleared from the box with the second ball contested.
4. **Carries.** Distance and speed scale with `tempo`, capped at the player's pace. Dispossession rises with pressure, with the channel block on entries, and inside the box.
5. **Shots.** The true scoring chance is `sigmoid(−1.9 − 0.11 × distance + 1.6 × goal_angle)`. Headers are ×0.6, shots under pressure ×0.8, and the result is scaled by `finishing`. A shot is a goal, saved, off target or blocked, and saves and blocks can produce rebounds.

Kickoffs (at the start of each half and after goals) are taken by the team's striker and played backward into midfield.

Receivers, pressers, tacklers and interceptors are the nearest players in each team's current shape (`sim/formations.py`). The shape is the formation template shifted with the ball, with its line set by `def_line`, spread by `width`, and the holding midfielder lowered by `pivot_drop`.

**Phases** change formation, roles or tactics at a match time. **Substitutions** put the incoming player in the outgoing player's role.

## Position snapshots

`sim/positions.py` replays the ball track each second. Every player heads for their slot in the team shape, except the ball holder (who stays on the ball), the intended receiver (who heads for the landing point) and the nearest defender within 18 m (who closes down). Each second a player covers 30% of the remaining distance (70% when chasing), capped by their personal top speed. Sprints therefore come from transitions and long balls, not from a script.

## The demo match (m-001)

Harbour: high press (`press_height 0.88`, `press_intensity 0.78`), left-channel preference (`0.62`). Kestrel: low press and poor press resistance (`buildup_security 0.18`). At half-time Kestrel bring on a centre-back, switch from 4-3-3 to 5-3-2 and drop the pivot 7 m deeper (`pivot_drop 7`, `buildup_security` +0.03).

`sim/story.py` checks the PRD story against raw event counts. Averaged over 60 seeds, the config alone produces:

| Effect | Mean over 60 seeds |
| --- | --- |
| Harbour H1 final-third entries via left | ~60% |
| Harbour left entries, H1 → H2 | about −40% (back five) |
| Kestrel own-third losses, H1 → H2 | about −13% (deeper pivot: little effect) |
| Mean ball-win x, H1 | Harbour ~53 m, Kestrel ~27 m |

The scoreline is random. `find-seed` scans seeds in order and pins the first one whose sampled events pass every check: HT 1–0, FT 1–1, Harbour scoring after 15', Kestrel equalizing after 55', and the effects above. That seed is **433** (Harbour 34', Kestrel 58'). The tactics stay the same whatever the seed. The seed only decides which random match is shown.

## Control matches

- **m-002**: Kestrel at home press high and favour the right. Harbour play a 4-4-2 mid block and make no tactical change.
- **m-003**: an even game. Both teams play a mid block, prefer the centre and change nothing.

Both use their own fixed seeds with no seed search. If the analytics find a story in these matches, it has to come from the data.

## Known limitations

- No corners, offsides or penalties. Set pieces are reduced to restart delays plus possession changes.
- Player tracking is a shape model rather than physics. Distances (~8 km per outfielder) run low and sprint time runs high compared with real tracking data.
- Pass completion (71–78%) runs a little below elite-league levels.
