"""PawPal+ core system: owners, pets, care tasks, and the daily scheduler."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, time, timedelta
from enum import IntEnum
from itertools import combinations
from pathlib import Path

FREQUENCIES = ("daily", "weekly")
FREQUENCY_DAYS = {"daily": 1, "weekly": 7}
MINUTES_PER_DAY = 24 * 60
# Small tie-breaker so essential care wins over optional care at equal priority.
CATEGORY_BONUS = {"meds": 3, "feeding": 2, "walk": 1}


def _to_minutes(t: time) -> int:
    """Convert a time of day to minutes since midnight."""
    return t.hour * 60 + t.minute


def _to_time(minutes: int) -> time:
    """Convert minutes since midnight back to a time of day."""
    return time(minutes // 60, minutes % 60)


def _overlaps(a: Task, b: Task) -> bool:
    """Return True if two tasks' preferred time windows overlap.

    Each window runs from preferred_time to preferred_time + duration_minutes. Two
    windows overlap when each one starts before the other ends. Back-to-back tasks
    (one ends exactly when the next starts) don't count.

    Args:
        a: A task with a preferred_time.
        b: Another task with a preferred_time.

    Returns:
        True if the windows share at least one minute, otherwise False.

    Example:
        Walk 07:30 (30 min) and Meds 07:45 (5 min) -> True
        Walk 07:30 (30 min) and Meds 08:00 (5 min) -> False (back-to-back)
    """
    a_start, b_start = _to_minutes(a.preferred_time), _to_minutes(b.preferred_time)
    return a_start < b_start + b.duration_minutes and b_start < a_start + a.duration_minutes


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
    due_date: date | None = None  # None means due now; set on follow-up occurrences
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

    def mark_complete(self, day: date | None = None) -> Task | None:
        """Record that this task was done and create its next occurrence.

        This task becomes a history record (it's never due again). A copy is made
        with next_occurrence(), due 1 day later for "daily" or 7 days later for
        "weekly", and added to the same pet automatically.

        Args:
            day: The day the task was done. Defaults to today.

        Returns:
            The new next-occurrence Task, or None if this task was already complete
            (so completing it twice never creates duplicates).

        Example:
            A daily walk completed on Oct 25 creates a walk due Oct 26.
        """
        if self.last_completed is not None:
            return None
        day = day or date.today()
        self.last_completed = day
        next_task = self.next_occurrence(day)
        if self.pet is not None:
            self.pet.add_task(next_task)
        return next_task

    def next_occurrence(self, completed_on: date) -> Task:
        """Return a fresh copy of this task for its next due date.

        The copy keeps the title, category, duration, priority, frequency, preferred
        time and required flag. It gets a new id, no completion date, and no pet
        (mark_complete() attaches it).

        Args:
            completed_on: The day this occurrence was done.

        Returns:
            A new Task due completed_on + 1 day ("daily") or + 7 days ("weekly").
        """
        return replace(
            self,
            id=uuid.uuid4().hex[:8],
            last_completed=None,
            due_date=completed_on + timedelta(days=FREQUENCY_DAYS[self.frequency]),
            pet=None,
        )

    def reset(self) -> None:
        """Clear completion history so the task is due again."""
        self.last_completed = None

    def is_completed(self, day: date) -> bool:
        """Return True if the task was already done on the given day."""
        return self.last_completed == day

    def is_due_today(self, day: date) -> bool:
        """Return True if this occurrence still needs doing on the given day.

        A completed occurrence is never due again; its follow-up copy takes over.
        An open task is due once its due_date has arrived, so a missed task stays
        due on later days. A task with no due_date is due right away.

        Args:
            day: The day to check.

        Returns:
            True if the task is open and day >= due_date (or due_date is None).
        """
        if self.last_completed is not None:
            return False
        return self.due_date is None or day >= self.due_date

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

    def to_dict(self) -> dict:
        """Return a JSON-safe dict of this task (without the pet back-reference)."""
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "duration_minutes": self.duration_minutes,
            "priority": int(self.priority),
            "frequency": self.frequency,
            "preferred_time": self.preferred_time.isoformat() if self.preferred_time else None,
            "is_required": self.is_required,
            "last_completed": self.last_completed.isoformat() if self.last_completed else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        """Rebuild a task from to_dict() output."""
        preferred = data.get("preferred_time")
        completed = data.get("last_completed")
        due = data.get("due_date")
        return cls(
            title=data["title"],
            category=data["category"],
            duration_minutes=data["duration_minutes"],
            priority=data.get("priority", Priority.MEDIUM),
            frequency=data.get("frequency", "daily"),
            preferred_time=time.fromisoformat(preferred) if preferred else None,
            is_required=data.get("is_required", False),
            last_completed=date.fromisoformat(completed) if completed else None,
            due_date=date.fromisoformat(due) if due else None,
            id=data.get("id") or uuid.uuid4().hex[:8],
        )


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

    def to_dict(self) -> dict:
        """Return a JSON-safe dict of this pet and its tasks."""
        return {
            "name": self.name,
            "species": self.species,
            "age": self.age,
            "special_needs": list(self.special_needs),
            "tasks": [task.to_dict() for task in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Pet:
        """Rebuild a pet (and its tasks' back-references) from to_dict() output."""
        return cls(
            name=data["name"],
            species=data["species"],
            age=data.get("age", 0),
            special_needs=list(data.get("special_needs", [])),
            tasks=[Task.from_dict(t) for t in data.get("tasks", [])],
        )


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

    def to_dict(self) -> dict:
        """Return a JSON-safe dict of this owner, their pets, and all tasks."""
        return {
            "name": self.name,
            "available_minutes": self.available_minutes,
            "day_start": self.day_start.isoformat(),
            "max_task_minutes": self.max_task_minutes,
            "pets": [pet.to_dict() for pet in self.pets],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Owner:
        """Rebuild an owner from to_dict() output."""
        return cls(
            name=data["name"],
            available_minutes=data.get("available_minutes", 0),
            day_start=time.fromisoformat(data.get("day_start", "08:00")),
            max_task_minutes=data.get("max_task_minutes"),
            pets=[Pet.from_dict(p) for p in data.get("pets", [])],
        )

    def save(self, path: str | Path) -> None:
        """Write this owner to a JSON file, replacing it atomically."""
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2))
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> Owner | None:
        """Read an owner from a JSON file, or return None if the file doesn't exist."""
        path = Path(path)
        if not path.exists():
            return None
        return cls.from_dict(json.loads(path.read_text()))


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
        collect_tasks -> sort_by_priority -> fit_to_time -> find_time_conflicts
            -> assign_times -> detect_conflicts
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

    def sort_by_time(self, tasks: list[Task]) -> list[Task]:
        """Return tasks ordered by preferred_time, earliest first.

        Uses sorted() with a lambda that builds a tuple key, compared left to right:
          1. preferred_time is None -> timed tasks (False) before untimed (True)
          2. preferred_time         -> earliest first (None becomes time.min so it
                                       can be compared; it never decides the order)
          3. -priority_score()      -> highest priority first to break ties

        Args:
            tasks: The tasks to sort. The list itself is not changed.

        Returns:
            A new sorted list.

        Example:
            [Dinner 18:00, Puzzle (none, low), Meds 08:00, Brushing (none, high)]
            -> [Meds, Dinner, Brushing, Puzzle]
        """
        return sorted(
            tasks,
            key=lambda t: (t.preferred_time is None, t.preferred_time or time.min, -t.priority_score()),
        )

    def filter_tasks(
        self,
        pet_name: str | None = None,
        completed: bool | None = None,
        day: date | None = None,
    ) -> list[Task]:
        """Return the owner's tasks that match every filter given.

        Any filter left as None is skipped, so with no filters every task is
        returned. Tasks keep their original order.

        Args:
            pet_name: Only tasks for this pet. Ignores case and extra spaces.
            completed: True for tasks completed on day, False for tasks not
                completed on day.
            day: The day completion is checked against. Defaults to today.

        Returns:
            A new list of matching tasks.

        Example:
            filter_tasks(pet_name="Rex", completed=False) -> Rex's tasks still to do today
        """
        day = day or date.today()
        return [
            task
            for task in self.owner.get_all_tasks()
            if (pet_name is None or (task.pet and task.pet.name.lower() == pet_name.strip().lower()))
            and (completed is None or task.is_completed(day) == completed)
        ]

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

    def find_time_conflicts(self, tasks: list[Task]) -> list[tuple[Task, Task]]:
        """Return every pair of tasks whose preferred time windows overlap.

        Tasks without a preferred_time are ignored. The rest are sorted with
        sort_by_time(), then every pair from itertools.combinations() is checked
        with _overlaps(). Works for the same pet and across different pets.
        Comparing every pair is O(n^2), which is instant for a day's 5-30 tasks
        and much easier to read than a sweep.

        Args:
            tasks: The tasks to check, from any pets.

        Returns:
            (earlier, later) pairs, earliest first. Empty if nothing overlaps.

        Example:
            Walk 07:30 (30 min), Supplement 07:45 (5 min), Dinner 18:00
            -> [(Walk, Supplement)]
        """
        timed = self.sort_by_time([t for t in tasks if t.preferred_time])
        return [(a, b) for a, b in combinations(timed, 2) if _overlaps(a, b)]

    def conflict_warnings(self, tasks: list[Task], focus: Task | None = None) -> list[str]:
        """Turn time conflicts into readable warning messages.

        This is the "lightweight" strategy: it warns instead of moving or removing
        tasks, and it never raises. If a task has bad data (e.g. a preferred_time
        that isn't a time), it returns one warning that the check was skipped.

        Args:
            tasks: The tasks to check, usually everything due that day.
            focus: If given, only report conflicts involving this task, e.g. to
                warn right after it's added.

        Returns:
            One message per conflict, saying whether it's the same pet or
            different pets. Empty if there are no conflicts.

        Example:
            "Time conflict, same pet (Mia): Mia: Thyroid meds (5 min, high) at
            08:00-08:05 overlaps Mia: Eye drops (5 min, high) at 08:00-08:05."
        """
        def window(t: Task) -> str:
            """Format a task's preferred time window, e.g. '07:30-08:00'."""
            end = (_to_minutes(t.preferred_time) + t.duration_minutes) % MINUTES_PER_DAY
            return f"{t.preferred_time:%H:%M}-{_to_time(end):%H:%M}"

        try:
            warnings: list[str] = []
            for a, b in self.find_time_conflicts(tasks):
                if focus is not None and focus is not a and focus is not b:
                    continue
                scope = f"same pet ({a.pet.name})" if a.pet and a.pet is b.pet else "different pets"
                warnings.append(f"Time conflict, {scope}: {a} at {window(a)} overlaps {b} at {window(b)}.")
            return warnings
        except (TypeError, ValueError, AttributeError) as err:
            return [f"Couldn't check tasks for time conflicts ({err}); review preferred times."]

    def detect_conflicts(self, slots: list[tuple[time, Task]]) -> list[str]:
        """Return warnings about the final schedule: over budget, moved tasks, past midnight."""
        warnings: list[str] = []
        total = sum(task.duration_minutes for _, task in slots)
        if total > self.owner.available_minutes:
            warnings.append(
                f"Required tasks need {total} min but only {self.owner.available_minutes} min "
                f"are available."
            )
        for start, task in sorted(slots, key=lambda slot: slot[0]):
            if task.preferred_time and start != task.preferred_time:
                warnings.append(
                    f"{task} moved from preferred {task.preferred_time:%H:%M} to {start:%H:%M}."
                )
            if _to_minutes(start) + task.duration_minutes > MINUTES_PER_DAY:
                warnings.append(f"{task} runs past midnight.")
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

        plan.warnings = self.conflict_warnings(chosen) + self.detect_conflicts(slots)
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
