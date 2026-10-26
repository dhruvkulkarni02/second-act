"""Measure grounding before and after the verifier, with Microsoft Foundry evaluations.

For each synthetic match, the half-time workflow runs online several times (no cache).
"Before" is every claim in the tactician's raw drafts; "after" is every claim in the
briefing that leaves the verifier. Each claim is scored two ways:

1. deterministic: untraceable numbers, unknown signal IDs, missing evidence;
2. Foundry's built-in groundedness evaluator (an LLM judge run by Foundry, separate
   from the verifier's own support check), given only the values the claim cites.

    PYTHONPATH=src python scripts/evaluate_grounding.py [--runs 3] [--out docs/evaluation]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from agent_framework import EvalItem, Message
from agent_framework.foundry import FoundryEvals

from second_act.agents.tactician import METRIC_NOTES
from second_act.agents.verifier import check_point, normalize_evidence
from second_act.analytics.signals import WMEAN_SUFFIX, SignalSet
from second_act.orchestration.agent_log import AgentLog
from second_act.orchestration.foundry import FoundrySettings, build_chat_client
from second_act.orchestration.halftime import run_halftime
from second_act.schema.briefing import BriefingDraft, Point
from second_act.schema.io import read_events, read_metadata, read_snapshots
from second_act.state import MatchState

ROOT = Path(__file__).resolve().parents[1]
QUERY = (
    "Write one half-time tactical claim for a football broadcast, grounded in the cited signals."
)


def claims(draft: BriefingDraft) -> list[tuple[str, Point]]:
    """The headline and every working / not-working point, with where they sit."""
    out = [("headline", Point(text=draft.headline, evidence=draft.headline_evidence))]
    for read in draft.teams:
        for kind in ("working", "not_working"):
            out += [(f"{read.team}.{kind}[{i}]", p) for i, p in enumerate(getattr(read, kind))]
    return out


def context(point: Point, signals: SignalSet) -> str:
    """The values a claim cites, with what each metric means (all the judge gets)."""
    lines = []
    for sid in normalize_evidence(point.evidence):
        sig = signals.get(sid)
        if sig is None:
            lines.append(f"{sid}: (no such signal)")
            continue
        note = METRIC_NOTES.get(sig.metric.removesuffix(WMEAN_SUFFIX), "")
        lines.append(f"{sid} = {sig.value:g} {sig.unit} ({note}; team {sig.team or 'both'})")
    return "Cited signals:\n" + ("\n".join(lines) if lines else "(none)")


def deterministic(point: Point, signals: SignalSet) -> list[str]:
    """Issue categories the verifier's first layer finds in one claim."""
    found = check_point(
        point.model_copy(update={"evidence": normalize_evidence(point.evidence)}), signals, "x"
    )
    kinds = []
    for issue in found:
        if "unknown signal" in issue.message:
            kinds.append("unknown_id")
        elif "does not match" in issue.message:
            kinds.append("untraceable_number")
        elif "no evidence" in issue.message:
            kinds.append("no_evidence")
        else:
            kinds.append("other")
    return kinds


async def one_run(match_id: str, run: int) -> dict:
    folder = ROOT / "data" / match_id
    state = MatchState.at_first_half_end(
        read_events(folder / "events.jsonl"),
        read_metadata(folder / "metadata.json"),
        read_snapshots(folder / "snapshots.jsonl.gz"),
    )
    log = AgentLog(session_id=f"eval-{match_id}-{run}", write=False)
    start = time.perf_counter()
    briefing = await run_halftime(state, offline=False, log=log, cache=None)
    latency = round(time.perf_counter() - start, 1)
    raw = [
        BriefingDraft.model_validate(r["output"])
        for r in log.records
        if r["event"] == "draft" and r.get("output")
    ]
    final = BriefingDraft.model_validate(
        briefing.model_dump(include=set(BriefingDraft.model_fields))
    )
    return {
        "match_id": match_id,
        "run": run,
        "state": state,
        "raw": raw,
        "final": final,
        "source": briefing.source,
        "removed": len(briefing.removed_claims),
        "latency_s": latency,
        "summary": log.summary(),
    }


def _judged(sample) -> tuple[str, str]:
    """(claim text the judge saw, the judge's reason) from an evaluator's raw sample."""
    try:
        sent = json.loads(sample["input"][0]["content"])
        verdict = json.loads(sample["output"][0]["content"])
        return sent.get("response", ""), str(verdict.get("reason", ""))[:500]
    except (KeyError, IndexError, TypeError, ValueError):
        return "", ""


async def generate(runs: int) -> tuple[list[dict], list[dict]]:
    """Run the half-time workflow online and return (claim rows, run summaries)."""
    matches = ["m-001", "m-002", "m-003"]
    sem = asyncio.Semaphore(3)

    async def guarded(m: str, r: int) -> dict:
        async with sem:
            return await one_run(m, r)

    results = await asyncio.gather(*(guarded(m, r) for m in matches for r in range(runs)))
    rows: list[dict] = []
    for res in results:
        signals = res["state"].signals
        sets = [("before", d) for d in res["raw"]] + [("after", res["final"])]
        for label, draft in sets:
            for where, point in claims(draft):
                rows.append(
                    {
                        "label": label,
                        "match_id": res["match_id"],
                        "run": res["run"],
                        "where": where,
                        "text": point.text,
                        "evidence": point.evidence,
                        "context": context(point, signals),
                        "deterministic": deterministic(point, signals),
                    }
                )
    runs_out = [
        {
            "match_id": r["match_id"],
            "run": r["run"],
            "source": r["source"],
            "drafts": len(r["raw"]),
            "removed_claims": r["removed"],
            "latency_s": r["latency_s"],
            "tokens_in": r["summary"]["tokens_in"],
            "tokens_out": r["summary"]["tokens_out"],
        }
        for r in results
    ]
    return rows, runs_out


async def main(runs: int, out: Path, reuse: bool) -> None:
    settings = FoundrySettings.from_env()
    out.mkdir(parents=True, exist_ok=True)
    claims_path, report_path = out / "claims.jsonl", out / "grounding.json"
    if reuse:
        rows = [json.loads(line) for line in claims_path.read_text().splitlines() if line]
        runs_out = json.loads(report_path.read_text())["runs"]
    else:
        rows, runs_out = await generate(runs)

    evals = FoundryEvals(
        client=build_chat_client(settings), evaluators=[FoundryEvals.GROUNDEDNESS], timeout=1800
    )
    foundry = {}
    for label in ("before", "after"):
        subset = [r for r in rows if r["label"] == label]
        items = [
            EvalItem(
                conversation=[Message("user", [QUERY]), Message("assistant", [r["text"]])],
                context=r["context"],
            )
            for r in subset
        ]
        result = await evals.evaluate(items, eval_name=f"Second Act grounding: {label} verifier")
        by_text: dict[str, list] = {}
        for it in result.items or []:
            for sc in it.scores:
                text, reason = _judged(sc.sample)
                by_text.setdefault(text, []).append((sc, reason))
        for r in subset:
            found = by_text.get(r["text"]) or []
            sc, reason = found.pop(0) if found else (None, "")
            r["groundedness"] = sc.score if sc else None
            r["passed"] = sc.passed if sc else None
            r["reason"] = reason
        scores = [r["groundedness"] for r in subset if r["groundedness"] is not None]
        foundry[label] = {
            "status": result.status,
            "counts": result.result_counts,
            "mean_score": round(sum(scores) / len(scores), 2) if scores else None,
            "report_url": result.report_url,
        }

    def det_summary(label: str) -> dict:
        subset = [r for r in rows if r["label"] == label]
        kinds = Counter(k for r in subset for k in r["deterministic"])
        return {
            "claims": len(subset),
            "claims_with_issues": sum(bool(r["deterministic"]) for r in subset),
            "issues": dict(kinds),
        }

    claims_path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = {
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "runs": runs_out,
        "deterministic": {"before": det_summary("before"), "after": det_summary("after")},
        "foundry_groundedness": foundry,
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "runs"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "evaluation")
    parser.add_argument("--reuse", action="store_true", help="re-evaluate saved claims only")
    args = parser.parse_args()
    asyncio.run(main(args.runs, args.out, args.reuse))
