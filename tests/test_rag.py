"""Tests for the RAG layer. A fake LLM is used, so no API key or network is needed."""

from datetime import date, time

import pytest

from pawpal_rag import (
    MAX_SUGGESTIONS,
    CareAdvisor,
    LLMError,
    PlanExplainer,
    Retriever,
    build_query,
    load_knowledge_base,
    tokenize,
    validate_suggestions,
)
from pawpal_system import Owner, Pet, Priority, Scheduler, Task


class FakeLLM:
    """Returns a canned reply (or raises) and records each prompt it was sent."""

    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error
        self.prompts = []

    def complete_json(self, system, prompt, schema):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture(scope="module")
def chunks():
    return load_knowledge_base()


@pytest.fixture(scope="module")
def retriever(chunks):
    return Retriever(chunks)


def top_sources(retriever, pet, k=5):
    return [c.source for c, _ in retriever.search(build_query(pet), species=pet.species, top_k=k)]


def suggestion(**overrides):
    item = {
        "title": "Thyroid meds",
        "category": "meds",
        "duration_minutes": 5,
        "priority": "high",
        "frequency": "daily",
        "is_required": True,
        "preferred_time": "08:00",
        "reason": "Thyroid medication should be given at a consistent time.",
        "source_id": "cat_thyroid.md#thyroid-medication",
        "confidence": 0.9,
    }
    item.update(overrides)
    return item


# --- Knowledge base and retrieval ---------------------------------------------------

def test_knowledge_base_splits_guides_into_sections(chunks):
    ids = {c.id for c in chunks}
    assert "cat_thyroid.md#thyroid-medication" in ids
    assert all(c.text and c.heading for c in chunks)
    assert len(ids) == len(chunks), "chunk ids must be unique"


def test_tokenize_strips_stopwords_and_plurals():
    assert tokenize("The walks and meals") == ["walk", "meal"]


@pytest.mark.parametrize(
    "pet, expected",
    [
        (Pet("Mia", "cat", 9, ["senior", "thyroid condition"]), "cat_thyroid.md"),
        (Pet("Luna", "cat", 5, ["diabetes"]), "cat_diabetes.md"),
        (Pet("Rex", "dog", 11, ["arthritis"]), "dog_senior.md"),
        (Pet("Bo", "dog", 0), "dog_puppy.md"),
        (Pet("Bun", "other", 2), "rabbit_care.md"),
    ],
)
def test_retrieval_finds_the_relevant_guide(retriever, pet, expected):
    assert expected in top_sources(retriever, pet, k=3)


def test_retrieval_never_returns_another_species(retriever):
    assert not any(s.startswith("dog_") for s in top_sources(retriever, Pet("Mia", "cat", 9), k=20))
    assert not any(s.startswith("cat_") for s in top_sources(retriever, Pet("Rex", "dog", 4), k=20))


def test_retrieval_ignores_unrelated_query(retriever):
    assert retriever.search("zzzz qqqq", species="cat") == []


def test_empty_knowledge_base_is_an_error():
    with pytest.raises(ValueError):
        Retriever([])


# --- Guardrail validator -------------------------------------------------------------

def test_validator_accepts_grounded_suggestion(chunks):
    pet = Pet("Mia", "cat", 9, ["thyroid condition"])
    valid, rejected = validate_suggestions([suggestion()], pet, chunks)
    assert rejected == []
    assert valid[0].task.title == "Thyroid meds"
    assert valid[0].task.priority == Priority.HIGH
    assert valid[0].task.preferred_time == time(8, 0)
    assert valid[0].source.source == "cat_thyroid.md"


@pytest.mark.parametrize(
    "overrides, why",
    [
        ({"source_id": "made_up.md#nope"}, "unknown source"),
        ({"duration_minutes": 0}, "out of range"),
        ({"duration_minutes": 600}, "out of range"),
        ({"category": "surgery"}, "unknown category"),
        ({"frequency": "hourly"}, "frequency"),
        ({"preferred_time": "8am"}, "isoformat"),
        ({"title": "  "}, "missing title"),
        ({"reason": ""}, "missing reason"),
        ({"confidence": 1.5}, "outside 0-1"),
        ({"confidence": "high"}, "could not convert"),
    ],
)
def test_validator_rejects_bad_suggestions(chunks, overrides, why):
    valid, rejected = validate_suggestions([suggestion(**overrides)], Pet("Mia", "cat", 9), chunks)
    assert valid == []
    assert len(rejected) == 1 and why in rejected[0].lower()


def test_validator_rejects_duplicates_of_existing_and_repeated_tasks(chunks):
    pet = Pet("Mia", "cat", 9)
    pet.add_task(Task("Thyroid meds", "meds", 5))
    valid, rejected = validate_suggestions(
        [suggestion(), suggestion(title="Brushing", category="grooming"), suggestion(title="brushing")],
        pet,
        chunks,
    )
    assert [s.task.title for s in valid] == ["Brushing"]
    assert len(rejected) == 2


def test_validator_caps_number_of_suggestions(chunks):
    raw = [suggestion(title=f"Task {i}") for i in range(MAX_SUGGESTIONS + 2)]
    valid, rejected = validate_suggestions(raw, Pet("Mia", "cat", 9), chunks)
    assert len(valid) == MAX_SUGGESTIONS
    assert len(rejected) == 2


# --- Care advisor ----------------------------------------------------------------------

def test_advisor_sends_retrieved_guidance_and_returns_valid_suggestions(retriever):
    pet = Pet("Mia", "cat", 9, ["senior", "thyroid condition"])
    llm = FakeLLM({"suggestions": [suggestion(), suggestion(title="Fly to the moon", source_id="x")]})
    advice = CareAdvisor(retriever, llm).advise(pet)

    assert "[cat_thyroid.md#thyroid-medication]" in llm.prompts[0]
    assert "Mia, cat, 9 years old" in llm.prompts[0]
    assert [s.task.title for s in advice.suggestions] == ["Thyroid meds"]
    assert len(advice.rejected) == 1
    assert advice.error is None


def test_accepted_suggestion_changes_the_schedule(retriever):
    owner = Owner("Jordan", available_minutes=60, day_start=time(7, 0))
    pet = Pet("Mia", "cat", 9, ["thyroid condition"])
    owner.add_pet(pet)
    pet.add_task(Task("Breakfast", "feeding", 10, Priority.HIGH, preferred_time=time(7, 0)))
    day = date(2026, 11, 17)
    before = Scheduler(owner).generate_plan(day)

    advice = CareAdvisor(retriever, FakeLLM({"suggestions": [suggestion()]})).advise(pet)
    for s in advice.suggestions:  # what the UI does when the owner accepts
        pet.add_task(s.task)
    after = Scheduler(owner).generate_plan(day)

    assert [t.title for _, t in before.scheduled] == ["Breakfast"]
    assert ("08:00", "Thyroid meds") in [(f"{start:%H:%M}", t.title) for start, t in after.scheduled]


def test_advisor_falls_back_when_llm_fails(retriever):
    advice = CareAdvisor(retriever, FakeLLM(error=LLMError("network down"))).advise(Pet("Rex", "dog", 4))
    assert advice.suggestions == []
    assert advice.error == "network down"
    assert advice.chunks, "retrieval still runs so the guidance can be shown"


def test_advisor_handles_malformed_llm_reply(retriever):
    advice = CareAdvisor(retriever, FakeLLM({"suggestions": "not a list"})).advise(Pet("Rex", "dog", 4))
    assert advice.suggestions == [] and advice.error


def test_advisor_without_llm_reports_ai_off(retriever):
    advice = CareAdvisor(retriever, None).advise(Pet("Rex", "dog", 4))
    assert advice.suggestions == [] and "off" in advice.error


# --- Plan explainer ----------------------------------------------------------------------

def _owner_with_plan():
    owner = Owner("Jordan", available_minutes=60, day_start=time(7, 0))
    pet = Pet("Mia", "cat", 9, ["thyroid condition"])
    owner.add_pet(pet)
    pet.add_task(Task("Thyroid meds", "meds", 5, Priority.HIGH, preferred_time=time(8, 0), is_required=True))
    return owner, Scheduler(owner).generate_plan(date(2026, 11, 17))


def test_explainer_keeps_valid_citations_and_drops_invented_ones(retriever):
    owner, plan = _owner_with_plan()
    llm = FakeLLM({
        "overview": "A short morning routine.",
        "task_notes": [
            {"task": "08:00 Mia: Thyroid meds", "note": "Same time daily.", "source_id": "cat_thyroid.md#thyroid-medication"},
            {"task": "08:00 Mia: Thyroid meds", "note": "Made up.", "source_id": "fake.md#x"},
        ],
    })
    result = PlanExplainer(retriever, llm).explain(plan, owner)

    assert result.used_ai
    assert "Scheduler output" in llm.prompts[0] and "Thyroid meds" in llm.prompts[0]
    assert result.notes[0][2].id == "cat_thyroid.md#thyroid-medication"
    assert result.notes[1][2] is None
    assert "cat_thyroid.md > Thyroid medication" in result.to_markdown()


def test_explainer_falls_back_to_rule_based_text(retriever):
    owner, plan = _owner_with_plan()
    result = PlanExplainer(retriever, FakeLLM(error=LLMError("no key"))).explain(plan, owner)
    assert not result.used_ai
    assert result.overview == plan.explain()
    assert result.error == "no key"


def test_explainer_falls_back_on_missing_fields(retriever):
    owner, plan = _owner_with_plan()
    result = PlanExplainer(retriever, FakeLLM({"task_notes": []})).explain(plan, owner)
    assert not result.used_ai and result.overview == plan.explain()


# --- Pre-listed answers ------------------------------------------------------------------

import json  # noqa: E402

from pawpal_rag import AnswerBank, life_stage  # noqa: E402


@pytest.fixture(scope="module")
def bank(chunks):
    return AnswerBank.load(chunks)


def _healthy(species):
    return Pet("Test", species, 4)


def test_every_pre_listed_answer_is_valid_and_cites_a_real_guide(bank, chunks):
    raw = json.loads((__import__("pawpal_rag").ANSWERS_FILE).read_text())["answers"]
    assert len(bank.answers) == len(raw), "an answer in answers.json was skipped as invalid"
    for answer in bank.answers:
        valid, rejected = validate_suggestions([{**answer.suggestion, "confidence": 0.5}], _healthy("dog"), chunks)
        assert rejected == [], f"{answer.id}: {rejected}"


def test_answer_bank_skips_answers_citing_missing_guides(tmp_path, chunks, bank):
    good = {"id": "ok", "species": ["dog"], **bank.answers[0].suggestion}
    bad = {**good, "id": "bad", "source_id": "nope.md#missing"}
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": [good, bad]}))
    assert [a.id for a in AnswerBank.load(chunks, path).answers] == ["ok"]


def test_senior_need_counts_as_senior_life_stage():
    assert life_stage(Pet("Mia", "cat", 6, ["Senior"])) == "senior"
    assert life_stage(Pet("Mia", "cat", 6)) == "adult"


@pytest.mark.parametrize(
    "pet, must_include",
    [
        (Pet("Mia", "cat", 11, ["thyroid condition"]), "Thyroid medication"),
        (Pet("Luna", "cat", 6, ["diabetes"]), "Insulin (morning)"),
        (Pet("Max", "dog", 7, ["diabetic"]), "Insulin (morning)"),
        (Pet("Rex", "dog", 10, ["arthritis"]), "Joint supplement"),
        (Pet("Bo", "dog", 0), "Potty break"),
        (Pet("Bun", "other", 2), "Refill hay and water"),
    ],
)
def test_answer_bank_matches_condition_and_life_stage(bank, pet, must_include):
    titles = [s["title"] for s in bank.match(pet)]
    assert must_include in titles
    assert len(titles) <= MAX_SUGGESTIONS


@pytest.mark.parametrize("species", ["dog", "cat", "other"])
def test_answer_bank_never_gives_medication_to_a_healthy_pet(bank, species):
    assert not [s for s in bank.match(_healthy(species)) if s["category"] == "meds"]


def test_answer_bank_skips_tasks_the_pet_already_has(bank):
    pet = Pet("Mia", "cat", 11, ["thyroid condition"])
    pet.add_task(Task("thyroid MEDICATION", "meds", 5))
    assert "Thyroid medication" not in [s["title"] for s in bank.match(pet)]


def test_advisor_uses_pre_listed_answers_without_claude(retriever, bank):
    advice = CareAdvisor(retriever, bank=bank).advise(Pet("Mia", "cat", 11, ["senior", "thyroid condition"]))
    assert advice.source_mode == "pre-listed answers"
    assert advice.error is None
    first = advice.suggestions[0]
    assert first.task.title == "Thyroid medication" and first.task.is_required
    assert first.source.id == "cat_thyroid.md#thyroid-medication"
    assert first.confidence == 0.9


def test_advisor_falls_back_to_pre_listed_answers_when_claude_fails(retriever, bank):
    advice = CareAdvisor(retriever, FakeLLM(error=LLMError("network down")), bank).advise(Pet("Rex", "dog", 10, ["arthritis"]))
    assert advice.source_mode == "pre-listed answers"
    assert advice.suggestions
    assert "network down" in advice.error and "pre-listed" in advice.error


def test_advisor_prefers_claude_when_it_works(retriever, bank):
    advice = CareAdvisor(retriever, FakeLLM({"suggestions": [suggestion()]}), bank).advise(
        Pet("Mia", "cat", 9, ["thyroid condition"])
    )
    assert advice.source_mode == "Claude"
    assert [s.task.title for s in advice.suggestions] == ["Thyroid meds"]


def test_explainer_cites_pre_listed_answers_without_claude(retriever, bank):
    owner = Owner("Jordan", available_minutes=60, day_start=time(7, 0))
    pet = Pet("Mia", "cat", 11, ["thyroid condition"])
    owner.add_pet(pet)
    pet.add_task(Task("Thyroid medication", "meds", 5, Priority.HIGH, is_required=True))
    pet.add_task(Task("Cuddles", "other", 10))
    plan = Scheduler(owner).generate_plan(date(2026, 11, 18))

    result = PlanExplainer(retriever, bank=bank).explain(plan, owner)

    assert result.mode == "pre-listed answers" and not result.used_ai and result.error is None
    cited = {label: source for label, _, source in result.notes}
    assert any("Thyroid medication" in label and src and src.source == "cat_thyroid.md" for label, src in cited.items())
    assert any("Cuddles" in label and src is None for label, src in cited.items())
