# Signals and formulas

Every signal comes from `analytics/signals.py`: plain, deterministic Python with no model calls. Run this to print a summary of one match:

```bash
second-act-signals m-001            # add --json signals.json to dump every signal
```

## Frames, thirds, channels, windows

- **Team frame.** Every team-owned coordinate is rotated so that team attacks toward x = 105. Rotating (not mirroring) keeps left on the attacker's left.
- **Thirds** (team frame): defensive x < 35, middle 35–70, final x ≥ 70.
- **Channels** (team frame): left y < 22.67, centre 22.67–45.33, right y ≥ 45.33.
- **Windows:** five minutes of period time, `w0`–`w8`. Stoppage time folds into `w8`, so it runs from 40:00 to the whistle.
- **Live prefixes:** with `compute_signals(..., until=(period, seconds))`, a window is only emitted once it's complete. The half signal (`p1`, `p2`) is then cumulative so far.

## Signal IDs

`sig-<metric>-<team>-<qualifier>-p<period>[w<window>]`. Team and qualifier are omitted when they don't apply. Examples:

| ID | Meaning |
| --- | --- |
| `sig-channel_entries-HAR-left-p1w4` | Harbour's left-channel entries, 20:00–25:00 of the first half |
| `sig-turnover_height-KES-p1` | Kestrel's mean ball-win height, whole first half |
| `sig-avg_position_x-KES-n14-p2` | Kestrel #14's mean on-ball x, second half |
| `sig-pass_pair-HAR-n3_n11-p1` | Passes between Harbour #3 and #11, first half |
| `sig-control_index-p2` | Control-vs-chaos index for both teams, second half |

Values are rounded when computed, according to their unit: counts to integers; pct, m, ratio, rating, index and km/h to 1 dp; per_min and actions to 2 dp; xG to 2 dp. Agents quote the rounded value, so the verifier can match numbers exactly within a small tolerance.

## Event signals (per window and per half)

| Metric | Unit | Definition |
| --- | --- | --- |
| `possession` | pct | Team's share of on-ball events (pass, carry, shot) |
| `possession` + `<zone>_third` | pct | Half only. On-ball events in the team's third Z, against the opponent's on-ball events in the same physical area (their mirror third) |
| `passes`, `pass_completion` | count, pct | Attempted passes; share completed |
| `field_tilt` | pct | Team's share of passes started in the final third (own frame) by both teams |
| `ppda` | ratio | Opponent passes started in the opponent's own defensive 60% (x < 63 in their frame), divided by the team's defensive actions in that area (x ≥ 42 in its frame). Defensive actions are won tackles, interceptions and fouls; a tackle that's a foul counts once, via its foul event. Omitted if there are no actions. Lower means a more intense press |
| `turnover_height` | m | Mean x (own frame) where the team wins the ball in open play (`possession_change` with outcome `open_play`) |
| `high_regains` | count | Open-play ball wins in the final third. The opponent losing the ball in its own third shows up here |
| `channel_entries` + channel | count | Completed passes and carries that start before x = 70 and end at or beyond it, by the channel of the end point |
| `final_third_entries` | count | Sum across channels |
| `channel_share` + channel | pct | Half only. Channel's share of the team's entries |
| `progressive_passes` | count | Completed passes that end at least 10 m closer to the centre of the opponent's goal |
| `shots`, `shots_on_target`, `goals` | count | On target means goal or saved |
| `xg` | xG | Sum of the xG of the team's shots (model below) |
| `pass_difficulty` | rating | Mean difficulty (0–100, model below) of the team's completed passes |
| `sequence_length` | actions | Mean length of the team's possession sequences. A sequence is a run of consecutive on-ball events by one team |
| `turnovers_per_min` | per_min | Open-play ball losses (the opponent's open-play wins) per minute of the scope |
| `control_index` | index | `100 × L / (L + 3 × T)`, where L = `sequence_length` and T = `turnovers_per_min`. High means long possessions with rare turnovers (control); low means short possessions and frequent turnovers (chaos). Without a team, the index uses the mean sequence length of both teams and the per-team turnover rate |

## Player signals (per half)

| Metric | Unit | Definition |
| --- | --- | --- |
| `avg_position_x`, `avg_position_y` + `n<shirt>` | m | Pass-network node: mean location of the player's on-ball events and pass receptions (own frame). Players with at least 5 touches |
| `pass_pair` + `n<a>_n<b>` | count | Completed passes between two players in either direction. Top five pairs per team |
| `pass_difficulty`, `difficult_passes` + `n<shirt>` | rating, count | Mean difficulty of the player's completed passes, and how many rated 60 or more. Players with at least 10 completed passes |
| `max_shot_speed` + `n<shirt>` | km/h | Fastest shot by each shooter, from event `speed_kmh` |
| `fast_shots` | count | Team shots above 100 km/h |

The full pass network (all nodes and edges) is available as a structured object from `pass_network(events, meta, team, period)`.

## Snapshot signals (when position snapshots exist)

| Metric | Unit | Definition |
| --- | --- | --- |
| `def_line_height` | m | Out of possession only: mean, over snapshots, of the average x of the team's four deepest outfield players (own frame). Possession at a snapshot comes from the latest on-ball or possession-change event |
| `team_width` | m | Mean, over snapshots, of the gap between the team's widest and narrowest outfield players (largest y minus smallest y) |
| `sprints` | count | Sprint efforts: runs of consecutive 1 Hz samples above 25 km/h, counted once each when they start. Per window for the team; per half for each player (`n<shirt>`) |
| `top_speed` + `n<shirt>` | km/h | The player's fastest sample in the half |
| `distance` + `n<shirt>` | km | Distance covered in the half |

## Shot quality (xG)

```
xG = sigmoid(−1.25 − 0.10 × d + 1.25 × θ) × (0.7 if header)
```

`d` is the distance in metres from the shot to the centre of the goal. `θ` is the angle the 7.32 m goal mouth subtends at the shot location: `atan2(7.32·dx, dx² + dy² − 3.66²)`. Reference values: penalty spot ≈ 0.18, six-yard box centre ≈ 0.42, edge of the box centre (16.5 m) ≈ 0.09, 25 m ≈ 0.03. These coefficients are fixed and chosen by hand. They are not fitted to the synthetic data and differ from the generator's internal scoring model.

## Pass difficulty (0–100)

```
difficulty = 100 × (0.30 × min(distance / 40, 1)
                  + 0.20 × under_pressure
                  + 0.20 × clamp(forward / 30, 0, 1)
                  + 0.30 × min(bypassed / 5, 1))
```

- `forward` is the gain in x in metres.
- `bypassed` is the number of opponents, in the snapshot nearest the pass, who stand between the pass's start and end x and within 15 m laterally of the pass line.
- Without snapshots, `bypassed` is estimated as forward metres divided by 10.

## Known limitations

- Possession is a share of on-ball events, not of time on the ball.
- PPDA, turnover height and the control index get noisy over a single five-minute window. The tactician should prefer half-level values and use windows to show trends.
- Sprint counts inherit the generator's shape-model movement, which runs high compared with real tracking data (see docs/generator.md).
