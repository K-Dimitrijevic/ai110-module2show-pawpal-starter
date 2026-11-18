"""PawPal+ RAG layer: retrieve pet-care guidance, suggest tasks, and explain the plan.

Flow (see diagrams/rag_system.mmd):
    pet profile -> Retriever (BM25 over knowledge/*.md)
    -> CareAdvisor: pre-listed answers (knowledge/answers.json, the default) or Claude (optional)
    -> validate_suggestions (guardrail) -> owner review in the UI -> Scheduler
    -> PlanExplainer (cites the same guides) -> daily plan + explanation

Pre-listed answers need no API key and give the same result every time. Claude is
reached through the small LLMClient protocol, so tests can swap in a fake client.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Protocol

from pawpal_system import DailyPlan, Owner, Pet, Priority, Task

logger = logging.getLogger("pawpal.rag")

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
ANSWERS_FILE = KNOWLEDGE_DIR / "answers.json"
LOG_FILE = Path(__file__).parent / "logs" / "pawpal.log"
CATEGORIES = ("walk", "feeding", "meds", "enrichment", "grooming", "other")
DEFAULT_MODEL = "claude-opus-5-5"
MAX_SUGGESTIONS = 4
MAX_TASK_MINUTES = 240

DISCLAIMER = "Suggestions come from general pet-care guides and are not a substitute for advice from your vet."


def setup_logging(level: int = logging.INFO) -> None:
    """Log PawPal+ activity to logs/pawpal.log (and stderr). Safe to call more than once."""
    root = logging.getLogger("pawpal")
    if root.handlers:
        return
    LOG_FILE.parent.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in (logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    root.setLevel(level)


# --- Knowledge base ------------------------------------------------------------

@dataclass(frozen=True)
class Chunk:
    """One section of a guide: everything under a single '## ' heading."""

    id: str  # e.g. "cat_thyroid.md#thyroid-medication"
    source: str  # file name
    title: str  # guide title (the '# ' heading)
    heading: str  # section heading
    text: str

    @property
    def species(self) -> str:
        """Species tag from the file name prefix: 'dog', 'cat', 'rabbit', or 'general'."""
        return self.source.split("_", 1)[0]

    @property
    def label(self) -> str:
        """Human-readable citation, e.g. 'cat_thyroid.md > Thyroid medication'."""
        return f"{self.source} > {self.heading}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_knowledge_base(directory: Path = KNOWLEDGE_DIR) -> list[Chunk]:
    """Split every markdown guide in directory into one Chunk per '## ' section."""
    chunks: list[Chunk] = []
    for path in sorted(Path(directory).glob("*.md")):
        title, heading, lines = path.stem, None, []

        def flush() -> None:
            body = " ".join(line.strip() for line in lines if line.strip())
            if heading and body:
                chunks.append(Chunk(f"{path.name}#{_slug(heading)}", path.name, title, heading, body))

        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                flush()
                heading, lines = line[3:].strip(), []
            elif line.startswith("# "):
                title = line[2:].strip()
            else:
                lines.append(line)
        flush()
    logger.info("Loaded %d chunks from %s", len(chunks), directory)
    return chunks


# --- Retrieval -------------------------------------------------------------------

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "in", "is", "it",
    "of", "on", "or", "the", "to", "with", "this", "that", "should", "can", "do", "day",
}


def tokenize(text: str) -> list[str]:
    """Lowercase words with stopwords removed and a light plural strip ('walks' -> 'walk')."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for word in words:
        if word in STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


class Retriever:
    """Okapi BM25 search over knowledge-base chunks, filtered by species."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        if not chunks:
            raise ValueError("Knowledge base is empty; add markdown guides to knowledge/.")
        self.chunks = chunks
        self.k1, self.b = k1, b
        # Index the guide title and heading too, so "Senior Cat Care" matches "senior cat".
        self._docs = [Counter(tokenize(f"{c.title} {c.heading} {c.text}")) for c in chunks]
        self._lengths = [sum(doc.values()) for doc in self._docs]
        self._avg_len = sum(self._lengths) / len(self._lengths)
        df = Counter(term for doc in self._docs for term in doc)
        n = len(chunks)
        self._idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    @staticmethod
    def species_allowed(chunk: Chunk, species: str | None) -> bool:
        """Dog guides only for dogs, cat guides only for cats; general guides for everyone."""
        if species is None or chunk.species == "general":
            return True
        species = species.lower()
        if species in ("dog", "cat"):
            return chunk.species == species
        return chunk.species not in ("dog", "cat")

    def score(self, query: str, index: int) -> float:
        doc, length = self._docs[index], self._lengths[index]
        total = 0.0
        # Repeating a word in the query weights it more (build_query repeats special needs).
        for term, weight in Counter(tokenize(query)).items():
            tf = doc.get(term, 0)
            if tf:
                norm = tf + self.k1 * (1 - self.b + self.b * length / self._avg_len)
                total += weight * self._idf[term] * tf * (self.k1 + 1) / norm
        return total

    def search(self, query: str, species: str | None = None, top_k: int = 5) -> list[tuple[Chunk, float]]:
        """Return up to top_k (chunk, score) pairs with a positive score, best first."""
        scored = [
            (chunk, self.score(query, i))
            for i, chunk in enumerate(self.chunks)
            if self.species_allowed(chunk, species)
        ]
        results = sorted((pair for pair in scored if pair[1] > 0), key=lambda pair: -pair[1])[:top_k]
        logger.info(
            "Retrieved for %r (species=%s): %s",
            query, species, [f"{c.id} ({s:.2f})" for c, s in results],
        )
        return results


def age_group(pet: Pet) -> str:
    """Return 'puppy'/'kitten', 'senior', or 'adult' based on species and age."""
    species = pet.species.lower()
    if pet.age < 1:
        return "kitten" if species == "cat" else "puppy"
    if (species == "dog" and pet.age >= 8) or (species == "cat" and pet.age >= 10):
        return "senior"
    return "adult"


def life_stage(pet: Pet) -> str:
    """Like age_group, but an owner-entered 'senior' special need counts as senior too."""
    if any("senior" in need.lower() for need in pet.special_needs):
        return "senior"
    return age_group(pet)


def species_group(species: str) -> str:
    """'dog', 'cat', or 'other' for any other animal."""
    species = species.lower()
    return species if species in ("dog", "cat") else "other"


def build_query(pet: Pet) -> str:
    """Turn a pet's profile and current tasks into a search query.

    Special needs and life stage are repeated so they outweigh generic care words:
    a diabetic cat should get the insulin guidance before general feeding tips.
    """
    group = age_group(pet)
    parts = [pet.species, *[group] * (2 if group == "adult" else 3)]
    parts += [need for need in pet.special_needs if need.lower() != group for _ in range(3)]
    parts += [f"{t.title} {t.category}" for t in pet.tasks]
    parts += ["daily care exercise feeding"]
    return " ".join(parts)


# --- LLM client ----------------------------------------------------------------

class LLMError(RuntimeError):
    """The LLM call failed or returned something unusable. Callers fall back to rule-based output."""


class LLMClient(Protocol):
    def complete_json(self, system: str, prompt: str, schema: dict) -> dict: ...


class ClaudeClient:
    """Calls Claude with a JSON schema so the reply is guaranteed to be parseable JSON."""

    def __init__(self, model: str | None = None, effort: str = "medium") -> None:
        try:
            import anthropic
        except ImportError as err:  # pragma: no cover - depends on the environment
            raise LLMError("The 'anthropic' package is not installed (pip install -r requirements.txt).") from err
        self._anthropic = anthropic
        self.model = model or os.environ.get("PAWPAL_MODEL", DEFAULT_MODEL)
        self.effort = effort
        self._client = anthropic.Anthropic(max_retries=2, timeout=120.0)
        # The SDK only complains about missing credentials at request time, so check now.
        if not any(getattr(self._client, attr, None) for attr in ("api_key", "auth_token", "credentials")):
            raise LLMError("No Anthropic API key found. Set ANTHROPIC_API_KEY to enable AI features.")

    def complete_json(self, system: str, prompt: str, schema: dict) -> dict:
        anthropic = self._anthropic
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                # If a safety classifier declines, the API retries on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError as err:
            raise LLMError("No valid Anthropic API key. Set ANTHROPIC_API_KEY to enable AI features.") from err
        except anthropic.RateLimitError as err:
            raise LLMError("Rate limited by the Anthropic API; try again in a minute.") from err
        except anthropic.APIStatusError as err:
            raise LLMError(f"Anthropic API error {err.status_code}: {err.message}") from err
        except anthropic.APIConnectionError as err:
            raise LLMError("Couldn't reach the Anthropic API (network error).") from err
        except anthropic.AnthropicError as err:  # e.g. no credentials configured at all
            raise LLMError(f"Anthropic client error: {err}") from err

        logger.info(
            "LLM call model=%s stop=%s in=%s out=%s request_id=%s",
            response.model, response.stop_reason, response.usage.input_tokens,
            response.usage.output_tokens, getattr(response, "_request_id", None),
        )
        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise LLMError("The model's reply was cut off.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            raise LLMError("The model returned invalid JSON.") from err


# --- Care Advisor (task suggestions) -----------------------------------------------

@dataclass
class Suggestion:
    """A validated task suggestion waiting for the owner's approval."""

    pet_name: str
    task: Task
    reason: str
    source: Chunk
    confidence: float  # the model's 0-1 rating of how well the source supports this task


@dataclass
class Advice:
    """Everything the advisor produced for one pet."""

    pet_name: str
    chunks: list[Chunk] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # why each discarded suggestion failed
    error: str | None = None  # set when the LLM couldn't be used
    source_mode: str = ""  # "pre-listed answers" or "Claude"


SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "duration_minutes": {"type": "integer"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                    "frequency": {"type": "string", "enum": ["daily", "weekly"]},
                    "is_required": {"type": "boolean"},
                    "preferred_time": {"type": "string", "description": "HH:MM, or empty for flexible"},
                    "reason": {"type": "string"},
                    "source_id": {"type": "string"},
                    "confidence": {
                        "type": "number",
                        "description": "0 to 1: how directly the cited excerpt supports this task for this pet",
                    },
                },
                "required": [
                    "title", "category", "duration_minutes", "priority", "frequency",
                    "is_required", "preferred_time", "reason", "source_id", "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}

ADVISOR_SYSTEM = f"""You are PawPal+'s care advisor. You suggest pet care tasks that are missing from an owner's schedule.

Rules:
- Base every suggestion ONLY on the guide excerpts provided. Do not use outside knowledge.
- Every suggestion must cite exactly one excerpt by its id in source_id.
- Do not suggest a task the pet already has, even under a slightly different name.
- Suggest at most {MAX_SUGGESTIONS} tasks, most important first. Return an empty list if the excerpts don't justify any new task.
- duration_minutes must be between 1 and {MAX_TASK_MINUTES}. Use preferred_time "HH:MM" only when the guide implies a time; otherwise "".
- Set is_required true only for medication the excerpts say must not be skipped.
- reason is one short sentence telling the owner why, in plain words.
- confidence (0 to 1) rates how directly the excerpt supports this task for THIS pet: 0.9+ when the excerpt names the pet's condition or life stage and the task, about 0.6 for general good practice, below 0.4 when it's a stretch (then leave it out).
- Never invent medications or doses. Suggest a task like "Thyroid meds" only if the pet's special needs mention that condition."""


def format_chunks(chunks: list[Chunk]) -> str:
    return "\n\n".join(f"[{c.id}] {c.title} - {c.heading}\n{c.text}" for c in chunks)


def describe_pet(pet: Pet) -> str:
    needs = ", ".join(pet.special_needs) or "none"
    tasks = "\n".join(
        f"- {t.title} ({t.category}, {t.duration_minutes} min, {t.priority.name.lower()}, {t.frequency})"
        for t in pet.tasks
    ) or "- (no tasks yet)"
    return (
        f"Pet: {pet.name}, {pet.species}, {pet.age} years old ({age_group(pet)}). "
        f"Special needs: {needs}.\nCurrent tasks:\n{tasks}"
    )


def validate_suggestions(raw: list[dict], pet: Pet, chunks: list[Chunk]) -> tuple[list[Suggestion], list[str]]:
    """Guardrail: keep only suggestions that are well-formed, grounded, and new.

    A suggestion is rejected if it cites an excerpt that wasn't retrieved, duplicates
    an existing task title, has an invalid field, or would exceed MAX_SUGGESTIONS.
    """
    by_id = {c.id: c for c in chunks}
    seen = {t.title.strip().lower() for t in pet.tasks}
    valid: list[Suggestion] = []
    rejected: list[str] = []

    for item in raw:
        title = str(item.get("title", "")).strip()
        label = title or "(untitled)"
        try:
            if not title:
                raise ValueError("missing title")
            if title.lower() in seen:
                raise ValueError("duplicates an existing task")
            source = by_id.get(item.get("source_id", ""))
            if source is None:
                raise ValueError(f"cites unknown source {item.get('source_id')!r}")
            if item.get("category") not in CATEGORIES:
                raise ValueError(f"unknown category {item.get('category')!r}")
            minutes = int(item.get("duration_minutes", 0))
            if not 1 <= minutes <= MAX_TASK_MINUTES:
                raise ValueError(f"duration {minutes} min is out of range")
            reason = str(item.get("reason", "")).strip()
            if not reason:
                raise ValueError("missing reason")
            confidence = float(item.get("confidence", -1))
            if not 0 <= confidence <= 1:
                raise ValueError(f"confidence {confidence} is outside 0-1")
            preferred = str(item.get("preferred_time") or "").strip()
            task = Task(
                title=title,
                category=item["category"],
                duration_minutes=minutes,
                priority=Priority.parse(item.get("priority", "medium")),
                frequency=item.get("frequency", "daily"),
                preferred_time=time.fromisoformat(preferred) if preferred else None,
                is_required=bool(item.get("is_required", False)),
            )
            if len(valid) >= MAX_SUGGESTIONS:
                raise ValueError("too many suggestions")
        except (ValueError, KeyError, TypeError) as err:
            rejected.append(f"{label}: {err}")
            logger.warning("Rejected suggestion for %s: %s: %s", pet.name, label, err)
            continue
        seen.add(title.lower())
        valid.append(Suggestion(pet.name, task, reason, source, confidence))
    return valid, rejected


# --- Pre-listed answers ---------------------------------------------------------------

ANSWER_FIELDS = (
    "title", "category", "duration_minutes", "priority", "frequency",
    "is_required", "preferred_time", "reason", "source_id",
)
# How well an answer fits a pet: written for its condition > its life stage > any pet of its species.
MATCH_CONFIDENCE = {"needs": 0.9, "stage": 0.75, "general": 0.6}


@dataclass(frozen=True)
class CareAnswer:
    """One pre-written care task, tied to the guide section it comes from."""

    id: str
    species: tuple[str, ...]  # "dog", "cat", "other"
    stages: tuple[str, ...]  # life stages it's written for; empty = any
    needs: tuple[str, ...]  # lowercase words to look for in special needs; empty = none
    suggestion: dict  # the task fields, in the same shape Claude returns

    def match_confidence(self, pet: Pet) -> float | None:
        """Return how well this answer fits the pet, or None if it doesn't apply."""
        if species_group(pet.species) not in self.species:
            return None
        needs_text = " ".join(pet.special_needs).lower()
        if self.needs and any(word in needs_text for word in self.needs):
            return MATCH_CONFIDENCE["needs"]
        if self.stages and life_stage(pet) in self.stages:
            return MATCH_CONFIDENCE["stage"]
        if not self.needs and not self.stages:
            return MATCH_CONFIDENCE["general"]
        return None


class AnswerBank:
    """The pre-listed answers, matched to a pet by species, life stage and special needs."""

    def __init__(self, answers: list[CareAnswer], chunks: list[Chunk]) -> None:
        if not answers:
            raise ValueError("The answer bank is empty; add answers to knowledge/answers.json.")
        self.answers = answers
        self.chunks_by_id = {c.id: c for c in chunks}

    @classmethod
    def load(cls, chunks: list[Chunk], path: Path = ANSWERS_FILE) -> AnswerBank:
        """Load answers.json, skipping (and logging) any answer that is incomplete or cites a missing guide."""
        known = {c.id for c in chunks}
        answers = []
        for item in json.loads(Path(path).read_text(encoding="utf-8"))["answers"]:
            missing = [f for f in ("id", "species", *ANSWER_FIELDS) if f not in item]
            if missing:
                logger.error("Skipping answer %s: missing %s", item.get("id", "?"), missing)
                continue
            if item["source_id"] not in known:
                logger.error("Skipping answer %s: source %r is not in the guides", item["id"], item["source_id"])
                continue
            answers.append(CareAnswer(
                id=item["id"],
                species=tuple(item["species"]),
                stages=tuple(item.get("stages", [])),
                needs=tuple(word.lower() for word in item.get("needs", [])),
                suggestion={f: item[f] for f in ANSWER_FIELDS},
            ))
        logger.info("Loaded %d pre-listed answers from %s", len(answers), path)
        return cls(answers, chunks)

    @property
    def chunks(self) -> list[Chunk]:
        return list(self.chunks_by_id.values())

    def source(self, answer: CareAnswer) -> Chunk:
        return self.chunks_by_id[answer.suggestion["source_id"]]

    def match(self, pet: Pet, retrieved_ids: set[str] | frozenset[str] = frozenset()) -> list[dict]:
        """Return up to MAX_SUGGESTIONS answers for the pet that it doesn't already have, best first.

        Order: best fit (condition, then life stage, then general), then answers whose
        guide section the retriever found for this pet, then priority, then file order.
        """
        existing = {t.title.strip().lower() for t in pet.tasks}
        ranked = []
        for order, answer in enumerate(self.answers):
            confidence = answer.match_confidence(pet)
            if confidence is None or answer.suggestion["title"].strip().lower() in existing:
                continue
            retrieved = answer.suggestion["source_id"] in retrieved_ids
            priority = int(Priority.parse(answer.suggestion["priority"]))
            ranked.append(((-confidence, not retrieved, -priority, order), answer, confidence))
        ranked.sort(key=lambda item: item[0])
        top = ranked[:MAX_SUGGESTIONS]
        logger.info(
            "Answer bank: %d answer(s) fit %s; suggesting %s",
            len(ranked), pet.name, [answer.id for _, answer, _ in top],
        )
        return [{**answer.suggestion, "confidence": confidence} for _, answer, confidence in top]

    def find(self, pet: Pet, title: str) -> CareAnswer | None:
        """The answer for this pet's species with this task title, if there is one."""
        group, title = species_group(pet.species), title.strip().lower()
        return next(
            (a for a in self.answers if group in a.species and a.suggestion["title"].strip().lower() == title),
            None,
        )


class CareAdvisor:
    """Retrieves guidance for a pet, then suggests tasks from Claude (if given) or the pre-listed answers.

    If Claude is selected but fails, it falls back to the pre-listed answers.
    """

    def __init__(
        self, retriever: Retriever, llm: LLMClient | None = None, bank: AnswerBank | None = None, top_k: int = 5
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.bank = bank
        self.top_k = top_k

    def advise(self, pet: Pet) -> Advice:
        advice = Advice(pet.name)
        results = self.retriever.search(build_query(pet), species=pet.species, top_k=self.top_k)
        advice.chunks = [chunk for chunk, _ in results]

        if self.llm is not None and advice.chunks:
            raw = self._ask_claude(pet, advice)
            if raw is not None:
                advice.source_mode = "Claude"
                advice.suggestions, advice.rejected = validate_suggestions(raw, pet, advice.chunks)
                return advice
            if self.bank is not None:
                advice.error = f"{advice.error} Showing pre-listed answers instead."

        if self.bank is not None:
            advice.source_mode = "pre-listed answers"
            raw = self.bank.match(pet, {c.id for c in advice.chunks})
            advice.suggestions, advice.rejected = validate_suggestions(raw, pet, self.bank.chunks)
            return advice

        if advice.error is None:
            advice.error = (
                "No relevant guidance found in the knowledge base." if not advice.chunks
                else "AI is turned off (no API client configured)."
            )
        return advice

    def _ask_claude(self, pet: Pet, advice: Advice) -> list[dict] | None:
        """Ask Claude for suggestions; on failure, record the error on advice and return None."""
        prompt = (
            f"{describe_pet(pet)}\n\nGuide excerpts:\n{format_chunks(advice.chunks)}\n\n"
            "Suggest missing tasks for this pet."
        )
        try:
            data = self.llm.complete_json(ADVISOR_SYSTEM, prompt, SUGGESTION_SCHEMA)
            raw = data.get("suggestions", [])
            if not isinstance(raw, list):
                raise LLMError("'suggestions' was not a list.")
        except LLMError as err:
            logger.error("Care advisor failed for %s: %s", pet.name, err)
            advice.error = str(err)
            return None
        logger.info("Advisor returned %d raw suggestion(s) for %s", len(raw), pet.name)
        return raw


# --- Plan Explainer -----------------------------------------------------------------

@dataclass
class Explanation:
    """A plan explanation: from Claude, from the pre-listed answers, or the scheduler's own rules."""

    overview: str
    notes: list[tuple[str, str, Chunk | None]] = field(default_factory=list)  # (task label, note, source)
    used_ai: bool = False
    error: str | None = None
    mode: str = "rule-based"  # "Claude", "pre-listed answers", or "rule-based"

    def to_markdown(self) -> str:
        lines = [self.overview]
        for label, note, source in self.notes:
            cite = f" _[{source.label}]_" if source else ""
            lines.append(f"- **{label}**: {note}{cite}")
        return "\n".join(lines)


EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "task_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "e.g. '08:00 Mia: Thyroid meds'"},
                    "note": {"type": "string"},
                    "source_id": {"type": "string", "description": "excerpt id, or empty if none applies"},
                },
                "required": ["task", "note", "source_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overview", "task_notes"],
    "additionalProperties": False,
}

EXPLAINER_SYSTEM = """You explain a pet owner's daily care schedule in plain, friendly language.

Rules:
- The schedule was already built by a rule-based scheduler. Explain it; do not change it.
- overview: 2-3 sentences on how the day is organized and anything the owner should watch (skipped tasks, conflicts).
- task_notes: one short note per scheduled or skipped task, in schedule order.
- When a guide excerpt supports a note, cite its id in source_id. Use "" when no excerpt applies. Never cite an id that isn't listed.
- Use only the guide excerpts for care advice; don't add outside medical claims."""


class PlanExplainer:
    """Explains a DailyPlan with citations: Claude if given, else the pre-listed answers' reasons."""

    def __init__(
        self, retriever: Retriever, llm: LLMClient | None = None, bank: AnswerBank | None = None, top_k: int = 4
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.bank = bank
        self.top_k = top_k

    def gather_chunks(self, owner: Owner) -> list[Chunk]:
        chunks: dict[str, Chunk] = {}
        for pet in owner.pets:
            for chunk, _ in self.retriever.search(build_query(pet), species=pet.species, top_k=self.top_k):
                chunks.setdefault(chunk.id, chunk)
        return list(chunks.values())

    def fallback(self, plan: DailyPlan, error: str | None = None) -> Explanation:
        """Explain from the pre-listed answers if there are any, else use the scheduler's own text."""
        if self.bank is not None:
            return self.from_answers(plan, error)
        return Explanation(overview=plan.explain(), used_ai=False, error=error)

    def from_answers(self, plan: DailyPlan, error: str | None = None) -> Explanation:
        """For each scheduled task that matches a pre-listed answer, give that answer's reason and source."""
        notes = []
        for start, task in sorted(plan.scheduled, key=lambda slot: slot[0]):
            why = plan.reasons.get(task.id, "")
            answer = self.bank.find(task.pet, task.title) if task.pet else None
            if answer:
                notes.append((f"{start:%H:%M} {task}", f"{answer.suggestion['reason']} ({why})", self.bank.source(answer)))
            else:
                notes.append((f"{start:%H:%M} {task}", why[:1].upper() + why[1:], None))
        for task, reason in plan.skipped:
            notes.append((f"Skipped {task}", reason[:1].upper() + reason[1:], None))
        cited = sum(1 for _, _, source in notes if source)
        overview = (
            f"Plan for {plan.plan_date:%A, %B %d}: {len(plan.scheduled)} task(s), {plan.total_minutes_used} min. "
            f"{cited} of them come from the care guides, with the source shown."
        )
        if plan.warnings:
            overview += f" Check the {len(plan.warnings)} warning(s) above."
        logger.info("Plan explained from pre-listed answers: %d note(s), %d cited", len(notes), cited)
        return Explanation(overview, notes, used_ai=False, error=error, mode="pre-listed answers")

    def explain(self, plan: DailyPlan, owner: Owner) -> Explanation:
        if self.llm is None:
            return self.fallback(plan, None if self.bank else "AI is turned off (no API client configured).")
        chunks = self.gather_chunks(owner)
        pets = "\n\n".join(describe_pet(p) for p in owner.pets)
        prompt = (
            f"{pets}\n\nScheduler output:\n{plan.explain()}\n\n"
            f"Guide excerpts:\n{format_chunks(chunks) or '(none)'}\n\nExplain this plan."
        )
        try:
            data = self.llm.complete_json(EXPLAINER_SYSTEM, prompt, EXPLAIN_SCHEMA)
            overview = str(data["overview"]).strip()
            raw_notes = data["task_notes"]
        except (LLMError, KeyError, TypeError) as err:
            logger.error("Plan explainer failed, using rule-based explanation: %s", err)
            return self.fallback(plan, str(err))

        by_id = {c.id: c for c in chunks}
        notes = []
        for item in raw_notes if isinstance(raw_notes, list) else []:
            source_id = str(item.get("source_id", "")).strip()
            source = by_id.get(source_id)
            if source_id and source is None:
                # Guardrail: drop citations to guides that weren't retrieved.
                logger.warning("Explainer cited unknown source %r; citation removed", source_id)
            notes.append((str(item.get("task", "")).strip(), str(item.get("note", "")).strip(), source))
        if not overview:
            return self.fallback(plan, "The AI explanation was empty.")
        logger.info("Plan explained with %d note(s), %d cited", len(notes), sum(1 for n in notes if n[2]))
        return Explanation(overview, notes, used_ai=True, mode="Claude")


def make_llm_client() -> tuple[LLMClient | None, str | None]:
    """Build the Claude client, or return (None, reason) so the app can run without AI."""
    try:
        return ClaudeClient(), None
    except LLMError as err:
        logger.warning("AI disabled: %s", err)
        return None, str(err)
    except Exception as err:  # e.g. the SDK rejecting the environment's configuration
        logger.warning("AI disabled: %s", err)
        return None, f"Couldn't start the Anthropic client: {err}"
