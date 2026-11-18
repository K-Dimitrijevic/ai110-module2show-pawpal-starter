"""Reliability evaluation for the PawPal+ AI Care Advisor.

Runs a fixed set of test pets with known right answers and measures:

  Retrieval (no API key needed, deterministic)
    - hit rate: the expected guide is in the top 3 results

  Care Advisor: the pre-listed answers by default, or Claude with --claude
  (needs ANTHROPIC_API_KEY); each case is run --runs times
    - expected-task rate: a suggestion matches what the guides say this pet needs
    - medication hallucination rate: meds suggested for a pet with no medical condition
    - guardrail pass rate: share of raw suggestions that survive validation
    - average confidence the model gave to the suggestions it kept
    - consistency: cases where every run agreed on whether the expected task was found
    - API errors

Usage:
    python evaluate_rag.py                  # retrieval + pre-listed answers (no key needed)
    python evaluate_rag.py --claude         # retrieval + Claude's suggestions (needs a key)
    python evaluate_rag.py --runs 3         # repeat each case 3 times (default 2)

Results are printed and saved to logs/eval_results.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pawpal_rag import (
    AnswerBank,
    CareAdvisor,
    LLMClient,
    Retriever,
    Suggestion,
    build_query,
    load_knowledge_base,
    make_llm_client,
    setup_logging,
)
from pawpal_system import Pet, Priority, Task

RESULTS_FILE = Path(__file__).parent / "logs" / "eval_results.json"


@dataclass
class EvalCase:
    name: str
    pet: Pet
    expected_source: str  # guide file that must be in the top 3 results
    expected_task: str | None = None  # regex a suggested title should match, if any
    meds_allowed: bool = True  # False for healthy pets: any meds suggestion is a hallucination


def _pet(name, species, age, needs=(), tasks=()) -> Pet:
    pet = Pet(name, species, age, list(needs))
    for task in tasks:
        pet.add_task(task)
    return pet


def eval_cases() -> list[EvalCase]:
    """Fresh pets each call, so one run's accepted tasks can't leak into the next."""
    breakfast = lambda: Task("Breakfast", "feeding", 10, Priority.HIGH, is_required=True)  # noqa: E731
    return [
        EvalCase("Senior cat, thyroid", _pet("Mia", "cat", 11, ["senior", "thyroid condition"], [breakfast()]),
                 "cat_thyroid.md", r"thyroid|med"),
        EvalCase("Diabetic cat", _pet("Luna", "cat", 6, ["diabetes"], [breakfast()]),
                 "cat_diabetes.md", r"insulin"),
        EvalCase("Arthritic senior dog", _pet("Rex", "dog", 10, ["arthritis"],
                 [breakfast(), Task("Morning walk", "walk", 40, Priority.HIGH)]),
                 "dog_senior.md", r"joint|supplement|stretch|range.of.motion|gentle|short"),
        EvalCase("Puppy", _pet("Bo", "dog", 0, [], [breakfast()]),
                 "dog_puppy.md", r"potty|training|social|lunch|meal|feed", meds_allowed=False),
        EvalCase("Diabetic dog", _pet("Max", "dog", 7, ["diabetes"], [breakfast()]),
                 "dog_diabetes.md", r"insulin"),
        EvalCase("Rabbit", _pet("Bun", "rabbit", 2, [], []),
                 "rabbit_care.md", r"hay|green|exercise|run|play|free.roam|clean|litter", meds_allowed=False),
        EvalCase("Healthy adult dog, full routine", _pet("Buddy", "dog", 4, [], [
                     breakfast(), Task("Dinner", "feeding", 10, Priority.HIGH, is_required=True),
                     Task("Morning walk", "walk", 30, Priority.HIGH), Task("Evening walk", "walk", 30),
                     Task("Training session", "enrichment", 10), Task("Brushing", "grooming", 10, frequency="weekly"),
                 ]),
                 "dog_general.md", None, meds_allowed=False),
        EvalCase("Healthy adult cat", _pet("Cleo", "cat", 4, [], [breakfast()]),
                 "cat_general.md", r"play|litter|scoop|brush|groom|dinner|meal|feed|water", meds_allowed=False),
    ]


# --- Retrieval ----------------------------------------------------------------------

def evaluate_retrieval(retriever: Retriever, cases: list[EvalCase], k: int = 3) -> dict:
    rows = []
    for case in cases:
        results = retriever.search(build_query(case.pet), species=case.pet.species, top_k=k)
        sources = [chunk.source for chunk, _ in results]
        rows.append({"case": case.name, "expected": case.expected_source, "hit": case.expected_source in sources,
                     "top": sources})
    hits = sum(r["hit"] for r in rows)
    return {"k": k, "hits": hits, "total": len(rows), "hit_rate": hits / len(rows), "cases": rows}


# --- Care Advisor -------------------------------------------------------------------

@dataclass
class RunResult:
    suggestions: list[Suggestion] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def raw_count(self) -> int:
        """Every raw suggestion is either kept or rejected by the validator."""
        return len(self.suggestions) + len(self.rejected)


def score_run(case: EvalCase, run: RunResult) -> dict:
    """Score one advisor run for one case. Pure function, so it's unit tested."""
    titles = [s.task.title for s in run.suggestions]
    expected_hit = None
    if case.expected_task:
        expected_hit = any(re.search(case.expected_task, t, re.IGNORECASE) for t in titles)
    meds = [s.task.title for s in run.suggestions if s.task.category == "meds"]
    return {
        "titles": titles,
        "confidences": [round(s.confidence, 2) for s in run.suggestions],
        "expected_hit": expected_hit,
        "meds_hallucination": bool(meds) and not case.meds_allowed,
        "raw": run.raw_count,
        "kept": len(run.suggestions),
        "rejected": run.rejected,
        "error": run.error,
    }


def evaluate_advisor(
    retriever: Retriever, llm: LLMClient | None = None, runs: int = 2, bank: AnswerBank | None = None
) -> dict:
    """Run every case `runs` times through the advisor (Claude if llm is given, else the answer bank)."""
    advisor = CareAdvisor(retriever, llm=llm, bank=bank)
    per_case = []
    for case_index, case in enumerate(eval_cases()):
        scored = []
        for _ in range(runs):
            pet = eval_cases()[case_index].pet  # fresh pet every run
            advice = advisor.advise(pet)
            error = advice.error if not advice.suggestions and advice.error else None
            run = RunResult(advice.suggestions, advice.rejected, error)
            scored.append(score_run(case, run))
            print(f"  {case.name:<34} {scored[-1]['titles'] or scored[-1]['error'] or '(no suggestions)'}")
        per_case.append({"case": case.name, "runs": scored})
    return summarize_advisor(per_case)


def summarize_advisor(per_case: list[dict]) -> dict:
    all_runs = [run for case in per_case for run in case["runs"]]
    ok_runs = [run for run in all_runs if not run["error"]]
    expected = [run["expected_hit"] for run in ok_runs if run["expected_hit"] is not None]
    raw = sum(run["raw"] for run in ok_runs)
    kept = sum(run["kept"] for run in ok_runs)
    confidences = [c for run in ok_runs for c in run["confidences"]]
    consistent = sum(
        1 for case in per_case
        if len({run["expected_hit"] for run in case["runs"] if not run["error"]}) <= 1
    )
    return {
        "runs": len(all_runs),
        "errors": len(all_runs) - len(ok_runs),
        "expected_task_rate": sum(expected) / len(expected) if expected else None,
        "meds_hallucination_rate": (
            sum(run["meds_hallucination"] for run in ok_runs) / len(ok_runs) if ok_runs else None
        ),
        "guardrail_pass_rate": kept / raw if raw else None,
        "rejected_total": raw - kept,
        "avg_confidence": sum(confidences) / len(confidences) if confidences else None,
        "consistent_cases": consistent,
        "total_cases": len(per_case),
        "cases": per_case,
    }


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=2, help="times to run each case (default 2)")
    parser.add_argument("--claude", action="store_true", help="evaluate Claude's suggestions (needs an API key)")
    args = parser.parse_args()

    setup_logging(logging.WARNING)
    chunks = load_knowledge_base()
    retriever = Retriever(chunks)
    results: dict = {"date": datetime.now().isoformat(timespec="seconds")}

    print("Retrieval (expected guide in top 3)")
    retrieval = evaluate_retrieval(retriever, eval_cases())
    for row in retrieval["cases"]:
        print(f"  {'PASS' if row['hit'] else 'FAIL'}  {row['case']:<34} expected {row['expected']}, got {row['top']}")
    results["retrieval"] = retrieval

    if args.claude:
        llm, ai_error = make_llm_client()
        if llm is None:
            print(f"\nCare Advisor (Claude): skipped ({ai_error})")
        else:
            results["source"] = f"Claude ({getattr(llm, 'model', '?')})"
            print(f"\nCare Advisor: {results['source']}, {args.runs} run(s) per case")
            results["advisor"] = evaluate_advisor(retriever, llm=llm, runs=args.runs)
    else:
        bank = AnswerBank.load(chunks)
        results["source"] = f"pre-listed answers ({len(bank.answers)} in knowledge/answers.json)"
        print(f"\nCare Advisor: {results['source']}, {args.runs} run(s) per case")
        results["advisor"] = evaluate_advisor(retriever, runs=args.runs, bank=bank)

    print("\nSummary")
    print(f"  Retrieval hit rate:         {retrieval['hits']}/{retrieval['total']} ({pct(retrieval['hit_rate'])})")
    adv = results.get("advisor")
    if adv:
        print(f"  Suggestion source:          {results['source']}")
        print(f"  Expected task suggested:    {pct(adv['expected_task_rate'])}")
        print(f"  Meds hallucination rate:    {pct(adv['meds_hallucination_rate'])} (healthy pets given meds)")
        print(f"  Guardrail pass rate:        {pct(adv['guardrail_pass_rate'])} ({adv['rejected_total']} rejected)")
        avg = adv["avg_confidence"]
        print(f"  Average confidence:         {'n/a' if avg is None else f'{avg:.2f}'}")
        print(f"  Consistent across runs:     {adv['consistent_cases']}/{adv['total_cases']} cases")
        print(f"  Errors:                     {adv['errors']}/{adv['runs']} runs")

    RESULTS_FILE.parent.mkdir(exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(results, indent=2))
    print(f"\nSaved to {RESULTS_FILE.relative_to(Path(__file__).parent)}")


if __name__ == "__main__":
    main()
