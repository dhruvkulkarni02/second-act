# Overlay cue contract

The integration point for a broadcast graphics system is `second_act.schema.Cue` (Pydantic). Every cue arrives on the session's SSE stream as `event: cue`, in every variant.

```json
{
  "cue_id": "c-0031",
  "match_time": "65:00",
  "duration_s": 10,
  "kind": "prediction_check",
  "audience": "casual",
  "language": "en",
  "club_focus": "KES",
  "headline": "Good news for Kestrel fans: It's working",
  "body": "It's working: Harbour's attacks down the left fell by 44% after the break.",
  "evidence": ["sig-channel_entries_wmean-HAR-left-p1", "sig-channel_entries_wmean-HAR-left-p2"],
  "prediction_id": "p-KES-1",
  "status": "confirmed"
}
```

| Field | Meaning |
| --- | --- |
| `cue_id` | Unique within a session, increasing |
| `match_time` | Match clock (`mm:ss`; the second half starts at `45:00`) when the cue applies |
| `duration_s` | Suggested time on screen: 8 live trigger, 6 player tag, 10 prediction check, 20 half-time, 30 full-time |
| `kind` | `live_trigger`, `player_tag`, `halftime_briefing`, `prediction_check` or `fulltime_recap` |
| `audience` | `analyst` (precise, every number) or `casual` (plain words) |
| `language` | `en` or `es` (Spanish uses decimal commas) |
| `club_focus` | `null`, or the code of the club whose point of view the text takes |
| `headline`, `body` | The text to show. Every number in it traces to `evidence` |
| `evidence` | Signal IDs the cue is based on, all present in the session's signal set |
| `prediction_id`, `status` | For `prediction_check` cues: which prediction, and its new status (`confirmed`, `contradicted`) |

A graphics system picks one `(audience, language, club_focus)` and drops the other variants. The full half-time briefing and full-time recap (all variants) also come as `briefing` and `recap` stream messages and from `/api/sessions/{id}/briefing` and `/api/sessions/{id}/recap`.
