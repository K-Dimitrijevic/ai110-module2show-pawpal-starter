"""Tests for the evaluation harness, so its reliability numbers can be trusted."""

import pytest

from evaluate_rag import eval_cases, evaluate_advisor, evaluate_retrieval
from pawpal_rag import Retriever, load_knowledge_base


def _sug(title, category, source_id, confidence=0.8):
    return {
        "title": title, "category": category, "duration_minutes": 5, "priority": "high",
        "frequency": "daily", "is_required": category == "meds", "preferred_time": "",
        "reason": "From the guide.", "source_id": source_id, "confidence": confidence,
    }


class ScriptedLLM:
    """Answers by pet name: right answer for Luna, a meds hallucination for Buddy, junk for Rex."""

    def complete_json(self, system, prompt, schema):
        if "Pet: Luna" in prompt:
            return {"suggestions": [_sug("Insulin (morning)", "meds", "cat_diabetes.md#insulin-and-meal-timing", 0.95)]}
        if "Pet: Buddy" in prompt:
            return {"suggestions": [_sug("Daily vitamins", "meds", "dog_general.md#feeding-schedule", 0.4)]}
        if "Pet: Rex" in prompt:
            return {"suggestions": [_sug("Laser therapy", "meds", "made_up.md#x")]}
        return {"suggestions": []}


@pytest.fixture(scope="module")
def retriever():
    return Retriever(load_knowledge_base())


def test_retrieval_eval_passes_every_case(retriever):
    result = evaluate_retrieval(retriever, eval_cases())
    failed = [row for row in result["cases"] if not row["hit"]]
    assert failed == [], failed


def test_advisor_eval_computes_metrics(retriever):
    result = evaluate_advisor(retriever, ScriptedLLM(), runs=2)
    luna = next(c for c in result["cases"] if c["case"] == "Diabetic cat")
    buddy = next(c for c in result["cases"] if c["case"].startswith("Healthy adult dog"))

    assert all(run["expected_hit"] for run in luna["runs"])
    assert all(run["meds_hallucination"] for run in buddy["runs"])
    # Only Luna (1 of 7 cases with an expected task) gets it right.
    assert result["expected_task_rate"] == pytest.approx(1 / 7)
    # Buddy's meds in 2 of 16 runs.
    assert result["meds_hallucination_rate"] == pytest.approx(2 / 16)
    # 6 raw suggestions (Luna, Buddy, Rex x2 runs); Rex's invented source is rejected.
    assert result["guardrail_pass_rate"] == pytest.approx(4 / 6)
    assert result["rejected_total"] == 2
    assert result["avg_confidence"] == pytest.approx((0.95 + 0.4) / 2)
    assert result["consistent_cases"] == 8
    assert result["errors"] == 0


def test_answer_bank_eval_scores_every_case(retriever):
    from pawpal_rag import AnswerBank, load_knowledge_base

    result = evaluate_advisor(retriever, runs=1, bank=AnswerBank.load(load_knowledge_base()))
    assert result["expected_task_rate"] == 1.0
    assert result["meds_hallucination_rate"] == 0.0
    assert result["errors"] == 0
