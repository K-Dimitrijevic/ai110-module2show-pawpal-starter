"""PawPal+ core system: owners, pets, care tasks, and the daily scheduler."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import date, time
from enum import IntEnum

FREQUENCIES = ("daily", "weekly")
MINUTES_PER_DAY = 24 * 60
# Small tie-breaker so essential care wins over optional care at equal priority.
CATEGORY_BONUS = {"meds": 3, "feeding": 2, "walk": 1}


def _to_minutes(t: time) -> int:
    """Convert a time of day to minutes since midnight."""
    return t.hour * 60 + t.minute


def _to_time(minutes: int) -> time:
    """Convert minutes since midnight back to a time of day."""
    return time(minutes // 60, minutes % 60)


class Priority(IntEnum):
    """Task priority; higher value means more important."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @classmethod
    def parse(cls, value: Priority | int | str) -> Priority:
        """Accept a Priority, an int (1-3), or a name like "high"."""
        if isinstance(value, str):
            return cls[value.strip().upper()]
        return cls(value)


@dataclass
class Task:
    """A single pet care activity (walk, feeding, meds, enrichment, grooming, ...)."""

    title: str
    category: str
    duration_minutes: int
    priority: Priority = Priority.MEDIUM
    frequency: str = "daily"  # "daily" | "weekly"; model "twice daily" as two tasks
    preferred_time: time | None = None
    is_required: bool = False
    last_completed: date | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    # Set by Pet.add_task(); excluded from repr/eq to avoid recursion.
    pet: Pet | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Normalize priority and validate duration and frequency."""
        self.priority = Priority.parse(self.priority)
        if self.duration_minutes <= 0:
            raise ValueError(f"duration_minutes must be positive, got {self.duration_minutes}")
        if self.frequency not in FREQUENCIES:
            raise ValueError(f"frequency must be one of {FREQUENCIES}, got {self.frequency!r}")

    def mark_complete(self, day: date | None = None) -> None:
        """Record that this task was done on the given day (default: today)."""
        self.last_completed = day or date.today()

    def reset(self) -> None:
        """Clear completion history so the task is due again."""
        self.last_completed = None

    def is_completed(self, day: date) -> bool:
        """Return True if the task was already done on the given day."""
        return self.last_completed == day

    def is_due_today(self, day: date) -> bool:
        """Return True if the task is due on day, based on frequency and last_completed."""
        if self.last_completed is None:
            return True
        days_since = (day - self.last_completed).days
        if self.frequency == "weekly":
            return days_since >= 7
        return days_since >= 1

    def priority_score(self) -> int:
        """Return a sortable score combining priority, required status, and category."""
        score = int(self.priority) * 10 + CATEGORY_BONUS.get(self.category.lower(), 0)
        if self.is_required:
            score += 100
        return score

    def __str__(self) -> str:
        """Return a short label like 'Rex: Walk (30 min, high)'."""
        pet = f"{self.pet.name}: " if self.pet else ""
        return f"{pet}{self.title} ({self.duration_minutes} min, {self.priority.name.lower()})"


@dataclass(eq=False)
class Pet:
    """An animal being cared for. Owns its list of care tasks."""

    name: str
    species: str
    age: int = 0
    special_needs: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Point any tasks passed to the constructor back at this pet."""
        for task in self.tasks:
            task.pet = self

    def add_task(self, task: Task) -> None:
        """Attach a task to this pet and set its back-reference."""
        task.pet = self
        self.tasks.append(task)

    def remove_task(self, task: Task) -> None:
        """Detach a task from this pet and clear its back-reference."""
        self.tasks.remove(task)
        task.pet = None

    def get_task(self, task_id: str) -> Task:
        """Return the task with the given id, or raise KeyError."""
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"{self.name} has no task with id {task_id!r}")

    def edit_task(self, task_id: str, **changes) -> None:
        """Update fields on the task with the given id."""
        task = self.get_task(task_id)
        for name in changes:
            if name in ("id", "pet") or not hasattr(task, name):
                raise AttributeError(f"cannot edit task field {name!r}")
        validated = replace(task, **changes)  # runs __post_init__; raises before any change
        for name in changes:
            setattr(task, name, getattr(validated, name))

    def get_pending_tasks(self, day: date) -> list[Task]:
        """Return tasks that are due on the given day."""
        return [task for task in self.tasks if task.is_due_today(day)]

    def summary(self) -> str:
        """Return a short human-readable description of the pet."""
        text = f"{self.name} ({self.species}, {self.age} yr) - {len(self.tasks)} task(s)"
        if self.special_needs:
            text += f"; needs: {', '.join(self.special_needs)}"
        return text


@dataclass(eq=False)
class Owner:
    """The pet owner, whose time and preferences constrain the plan."""

    name: str
    available_minutes: int = 0
    day_start: time = time(8, 0)
    max_task_minutes: int | None = None  # preference: skip tasks longer than this
    pets: list[Pet] = field(default_factory=list)

    def add_pet(self, pet: Pet) -> None:
        """Add a pet to this owner, ignoring duplicates."""
        if pet not in self.pets:
            self.pets.append(pet)

    def remove_pet(self, pet: Pet) -> None:
        """Remove a pet from this owner."""
        self.pets.remove(pet)

    def set_availability(self, minutes: int, day_start: time | None = None) -> None:
        """Set the daily time budget and, optionally, when the day starts."""
        if minutes < 0:
            raise ValueError("available minutes cannot be negative")
        self.available_minutes = minutes
        if day_start is not None:
            self.day_start = day_start

    def get_all_tasks(self) -> list[Task]:
        """Return every task across all of this owner's pets."""
        return [task for pet in self.pets for task in pet.tasks]


@dataclass
class DailyPlan:
    """The scheduler's output: what was scheduled, what was skipped, and why."""

    plan_date: date
    scheduled: list[tuple[time, Task]] = field(default_factory=list)  # (start_time, task)
    skipped: list[tuple[Task, str]] = field(default_factory=list)  # (task, reason)
    total_minutes_used: int = 0
    explanations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)  # task id -> why it was scheduled

    def add_entry(self, task: Task, start_time: time, reason: str) -> None:
        """Schedule a task at start_time and record why."""
        self.scheduled.append((start_time, task))
        self.reasons[task.id] = reason
        self.total_minutes_used += task.duration_minutes
        self.explanations.append(f"{start_time:%H:%M} {task}: {reason}")

    def skip(self, task: Task, reason: str) -> None:
        """Record that a task was left out of the plan and why."""
        self.skipped.append((task, reason))
        self.explanations.append(f"Skipped {task}: {reason}")

    def explain(self) -> str:
        """Return a readable summary of the reasoning behind the plan."""
        lines = [
            f"Plan for {self.plan_date:%A, %B %d}: {len(self.scheduled)} task(s), "
            f"{self.total_minutes_used} min total."
        ]
        lines += [f"- {line}" for line in self.explanations]
        lines += [f"! {warning}" for warning in self.warnings]
        return "\n".join(lines)

    def to_table(self) -> list[dict]:
        """Return rows suitable for display in a Streamlit table."""
        rows = [
            {
                "time": f"{start:%H:%M}",
                "pet": task.pet.name if task.pet else "",
                "task": task.title,
                "category": task.category,
                "minutes": task.duration_minutes,
                "priority": task.priority.name.lower(),
                "status": "scheduled",
                "why": self.reasons.get(task.id, ""),
            }
            for start, task in sorted(self.scheduled, key=lambda slot: slot[0])
        ]
        rows += [
            {
                "time": "",
                "pet": task.pet.name if task.pet else "",
                "task": task.title,
                "category": task.category,
                "minutes": task.duration_minutes,
                "priority": task.priority.name.lower(),
                "status": "skipped",
                "why": reason,
            }
            for task, reason in self.skipped
        ]
        return rows


class Scheduler:
    """Builds a DailyPlan for an owner from their pets' tasks and constraints.

    Each step takes its input and returns its output (no shared mutable state),
    and generate_plan() chains them:
        collect_tasks -> sort_by_priority -> fit_to_time -> assign_times -> detect_conflicts
    """

    def __init__(self, owner: Owner, strategy: str = "priority-first") -> None:
        """Create a scheduler that plans for the given owner."""
        self.owner = owner
        self.strategy = strategy

    def collect_tasks(self, day: date) -> list[Task]:
        """Gather tasks due on day from all of the owner's pets."""
        return [task for task in self.owner.get_all_tasks() if task.is_due_today(day)]

    def sort_by_priority(self, tasks: list[Task]) -> list[Task]:
        """Order tasks: required first, then by priority score, then shortest first."""
        return sorted(tasks, key=lambda t: (-t.priority_score(), t.duration_minutes))

    def fit_to_time(self, tasks: list[Task]) -> tuple[list[Task], list[tuple[Task, str]]]:
        """Split tasks into (chosen, skipped_with_reasons) within the owner's available minutes.

        Required tasks are always chosen, even if they exceed the budget.
        Expects tasks already sorted by priority, so important tasks claim time first.
        """
        budget = self.owner.available_minutes
        limit = self.owner.max_task_minutes
        chosen: list[Task] = []
        skipped: list[tuple[Task, str]] = []
        used = 0
        for task in tasks:
            if task.is_required:
                chosen.append(task)
                used += task.duration_minutes
            elif limit is not None and task.duration_minutes > limit:
                skipped.append((task, f"longer than your {limit}-minute task limit"))
            elif used + task.duration_minutes <= budget:
                chosen.append(task)
                used += task.duration_minutes
            else:
                left = max(budget - used, 0)
                skipped.append((task, f"needs {task.duration_minutes} min, only {left} min left"))
        return chosen, skipped

    def assign_times(self, tasks: list[Task]) -> list[tuple[time, Task]]:
        """Give each task a start time from owner.day_start, honoring preferred_time.

        Tasks run one after another. Tasks with a preferred_time are placed at that
        time (or as soon after as possible); flexible tasks fill the gaps before them
        in priority order. Tasks that would start after midnight are left out.
        """
        fixed = sorted((t for t in tasks if t.preferred_time), key=lambda t: t.preferred_time)
        flexible = [t for t in tasks if not t.preferred_time]
        cursor = _to_minutes(self.owner.day_start)
        slots: list[tuple[time, Task]] = []

        while fixed or flexible:
            next_fixed_at = _to_minutes(fixed[0].preferred_time) if fixed else None
            fits_before_fixed = (
                flexible
                and (next_fixed_at is None or cursor + flexible[0].duration_minutes <= next_fixed_at)
            )
            if fits_before_fixed:
                task = flexible.pop(0)
                start = cursor
            elif fixed:
                task = fixed.pop(0)
                start = max(cursor, next_fixed_at)
            else:
                # Remaining flexible tasks can't fit before the next fixed one; append after.
                task = flexible.pop(0)
                start = cursor
            if start >= MINUTES_PER_DAY:
                break
            slots.append((_to_time(start), task))
            cursor = start + task.duration_minutes
        return slots

    def detect_conflicts(self, slots: list[tuple[time, Task]]) -> list[str]:
        """Return warnings, e.g. over-budget required tasks or missed preferred times."""
        warnings: list[str] = []
        total = sum(task.duration_minutes for _, task in slots)
        if total > self.owner.available_minutes:
            warnings.append(
                f"Required tasks need {total} min but only {self.owner.available_minutes} min "
                f"are available."
            )
        previous_end = None
        for start, task in sorted(slots, key=lambda slot: slot[0]):
            start_min = _to_minutes(start)
            end_min = start_min + task.duration_minutes
            if previous_end is not None and start_min < previous_end:
                warnings.append(f"{task} overlaps the previous task.")
            if task.preferred_time and start != task.preferred_time:
                warnings.append(
                    f"{task} moved from preferred {task.preferred_time:%H:%M} to {start:%H:%M}."
                )
            if end_min > MINUTES_PER_DAY:
                warnings.append(f"{task} runs past midnight.")
            previous_end = end_min
        return warnings

    def generate_plan(self, day: date | None = None) -> DailyPlan:
        """Run the full pipeline and return the resulting DailyPlan."""
        day = day or date.today()
        plan = DailyPlan(plan_date=day)

        ordered = self.sort_by_priority(self.collect_tasks(day))
        chosen, skipped = self.fit_to_time(ordered)
        slots = self.assign_times(chosen)

        for start, task in slots:
            plan.add_entry(task, start, self._reason(task, start))
        placed = {task.id for _, task in slots}
        for task in chosen:
            if task.id not in placed:
                skipped.append((task, "no time left in the day"))
        for task, reason in skipped:
            plan.skip(task, reason)

        plan.warnings = self.detect_conflicts(slots)
        return plan

    def _reason(self, task: Task, start: time) -> str:
        """Explain in plain words why a task was scheduled when it was."""
        parts = ["required" if task.is_required else f"{task.priority.name.lower()} priority"]
        if task.preferred_time and start == task.preferred_time:
            parts.append("at your preferred time")
        elif task.preferred_time:
            parts.append(f"as close to {task.preferred_time:%H:%M} as possible")
        else:
            parts.append("fits in your available time")
        return ", ".join(parts)
