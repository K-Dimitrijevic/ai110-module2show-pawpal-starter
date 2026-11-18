"""Terminal demo of the AI Care Advisor: retrieval -> suggestions -> validation -> schedule -> explanation.

    python demo_rag.py            # suggestions from the pre-listed answers (no API key needed)
    python demo_rag.py --claude   # suggestions from Claude (needs ANTHROPIC_API_KEY)

Unlike the app, this demo accepts every suggestion that passes validation, so the
effect on the schedule is visible without clicking through the UI.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, time

from pawpal_rag import (
    AnswerBank,
    CareAdvisor,
    PlanExplainer,
    Retriever,
    build_query,
    load_knowledge_base,
    make_llm_client,
    setup_logging,
    validate_suggestions,
)
from pawpal_system import Owner, Pet, Priority, Scheduler, Task

DAY = date(2026, 11, 17)


def build_owner() -> Owner:
    owner = Owner("Jordan", available_minutes=120, day_start=time(7, 0))
    mia = Pet("Mia", "cat", age=11, special_needs=["senior", "thyroid condition"])
    rex = Pet("Rex", "dog", age=10, special_needs=["arthritis"])
    owner.add_pet(mia)
    owner.add_pet(rex)
    mia.add_task(Task("Breakfast", "feeding", 5, Priority.HIGH, preferred_time=time(7, 0), is_required=True))
    mia.add_task(Task("Puzzle feeder", "enrichment", 15, Priority.LOW))
    rex.add_task(Task("Breakfast", "feeding", 10, Priority.HIGH, preferred_time=time(7, 15), is_required=True))
    rex.add_task(Task("Morning walk", "walk", 40, Priority.HIGH, preferred_time=time(7, 30)))
    return owner


def print_plan(title: str, owner: Owner) -> None:
    plan = Scheduler(owner).generate_plan(DAY)
    print(f"\n{title}")
    for start, task in sorted(plan.scheduled, key=lambda slot: slot[0]):
        print(f"  {start:%H:%M}  {task}")
    for task, reason in plan.skipped:
        print(f"  skipped {task}: {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PawPal+ RAG pipeline demo")
    parser.add_argument("--claude", action="store_true", help="use Claude instead of the pre-listed answers")
    args = parser.parse_args()

    setup_logging(logging.WARNING)
    chunks = load_knowledge_base()
    retriever, bank = Retriever(chunks), AnswerBank.load(chunks)
    llm = None
    if args.claude:
        llm, ai_error = make_llm_client()
        print(f"Suggestion source: {'Claude' if llm else 'pre-listed answers (Claude unavailable: ' + str(ai_error) + ')'}")
    else:
        print(f"Suggestion source: pre-listed answers ({len(bank.answers)} in knowledge/answers.json)")

    owner = build_owner()
    print_plan("Schedule BEFORE suggestions:", owner)

    advisor = CareAdvisor(retriever, llm, bank)
    for pet in owner.pets:
        print("\n" + "=" * 70)
        print(f"Pet: {pet.name} ({pet.species}, {pet.age} yr, needs: {', '.join(pet.special_needs)})")
        print(f"Query: {build_query(pet)}")
        advice = advisor.advise(pet)
        print("Retrieved guide sections:")
        for chunk in advice.chunks:
            print(f"  - {chunk.label}")
        print(f"Suggestions (from {advice.source_mode or 'nowhere'}):")
        if advice.error:
            print(f"  note: {advice.error}")
        for sug in advice.suggestions:
            t = sug.task
            when = f" at {t.preferred_time:%H:%M}" if t.preferred_time else ""
            print(f"  + {t.title} ({t.category}, {t.duration_minutes} min, {t.priority.name.lower()}, "
                  f"{t.frequency}{when}{', required' if t.is_required else ''})")
            print(f"      why: {sug.reason}")
            print(f"      source: {sug.source.label}  (confidence {sug.confidence:.2f})")
            pet.add_task(t)  # the app asks the owner first; the demo accepts everything valid
        for why in advice.rejected:
            print(f"  x rejected by validator: {why}")

    print("\n" + "=" * 70)
    print_plan("Schedule AFTER accepting suggestions:", owner)

    plan = Scheduler(owner).generate_plan(DAY)
    explanation = PlanExplainer(retriever, llm, bank).explain(plan, owner)
    print("\n" + "=" * 70)
    note = f" (note: {explanation.error})" if explanation.error else ""
    print(f"Explanation, with sources (from {explanation.mode}){note}:")
    print(explanation.to_markdown())

    guardrail_check()


def guardrail_check() -> None:
    """Feed the validator one good and three bad suggestions, the kind of mistakes an LLM makes."""
    pet = Pet("Mia", "cat", 11, ["senior", "thyroid condition"])
    pet.add_task(Task("Breakfast", "feeding", 5))
    base = {"category": "meds", "duration_minutes": 5, "priority": "high", "frequency": "daily",
            "is_required": True, "preferred_time": "", "reason": "From the guide.", "confidence": 0.9}
    raw = [
        {**base, "title": "Thyroid meds", "preferred_time": "08:00", "source_id": "cat_thyroid.md#thyroid-medication"},
        {**base, "title": "Breakfast", "category": "feeding", "source_id": "cat_general.md#feeding-cats"},
        {**base, "title": "Daily vitamin injection", "source_id": "vitamins.md#injections"},
        {**base, "title": "Weekly weigh-in", "category": "other", "duration_minutes": 0,
         "source_id": "cat_thyroid.md#weight-and-appetite-monitoring"},
    ]
    valid, rejected = validate_suggestions(raw, pet, load_knowledge_base())
    print("\n" + "=" * 70)
    print("Guardrail check: 1 good + 3 bad suggestions for Mia (no AI needed)")
    print(f"  accepted: {[s.task.title for s in valid]}")
    for why in rejected:
        print(f"  rejected: {why}")


if __name__ == "__main__":
    main()
