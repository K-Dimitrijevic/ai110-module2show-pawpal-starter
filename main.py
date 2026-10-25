"""Temporary testing ground: build a sample owner and print today's schedule."""

from datetime import date, time

from pawpal_system import Owner, Pet, Priority, Scheduler, Task


def build_sample_owner() -> Owner:
    owner = Owner("Jordan", available_minutes=120, day_start=time(7, 0), max_task_minutes=45)

    rex = Pet("Rex", "dog", age=4)
    mia = Pet("Mia", "cat", age=9, special_needs=["senior", "thyroid condition"])
    owner.add_pet(rex)
    owner.add_pet(mia)

    # Added deliberately out of time order to exercise Scheduler.sort_by_time().
    rex.add_task(Task("Evening walk", "walk", 30, Priority.MEDIUM, preferred_time=time(18, 0)))
    mia.add_task(Task("Puzzle feeder", "enrichment", 15, Priority.LOW))
    mia.add_task(Task("Thyroid meds", "meds", 5, Priority.HIGH, preferred_time=time(8, 0), is_required=True))
    rex.add_task(Task("Long hike", "walk", 60, Priority.MEDIUM))
    rex.add_task(Task("Morning walk", "walk", 30, Priority.HIGH, preferred_time=time(7, 30)))
    mia.add_task(Task("Brushing", "grooming", 15, Priority.LOW, frequency="weekly"))
    rex.add_task(Task("Breakfast", "feeding", 10, Priority.HIGH, preferred_time=time(7, 0), is_required=True))
    # Deliberate clashes to exercise Scheduler.find_time_conflicts().
    # Same start time, different pets: Rex's Breakfast and Mia's Breakfast at 07:00.
    mia.add_task(Task("Breakfast", "feeding", 5, Priority.HIGH, preferred_time=time(7, 0), is_required=True))
    # Same start time, same pet: Mia's Thyroid meds and Eye drops at 08:00.
    mia.add_task(Task("Eye drops", "meds", 5, Priority.HIGH, preferred_time=time(8, 0)))
    # Overlapping (not identical) times, same pet: inside Rex's 07:30-08:00 Morning walk.
    rex.add_task(Task("Joint supplement", "meds", 5, Priority.HIGH, preferred_time=time(7, 45)))

    return owner


def print_schedule(owner: Owner, day: date) -> None:
    plan = Scheduler(owner).generate_plan(day)

    print("=" * 60)
    print(f"Today's Schedule for {owner.name} - {day:%A, %B %d, %Y}")
    print("=" * 60)
    print("Pets:")
    for pet in owner.pets:
        print(f"  - {pet.summary()}")
    print()

    print(f"{'Time':<7}{'Pet':<6}{'Task':<16}{'Min':>4}  {'Priority':<9}Why")
    print("-" * 60)
    for row in plan.to_table():
        if row["status"] == "scheduled":
            print(f"{row['time']:<7}{row['pet']:<6}{row['task']:<16}{row['minutes']:>4}  "
                  f"{row['priority']:<9}{row['why']}")
    print("-" * 60)
    print(f"Total: {plan.total_minutes_used} of {owner.available_minutes} available minutes")

    if plan.skipped:
        print("\nSkipped:")
        for task, reason in plan.skipped:
            print(f"  - {task}: {reason}")

    if plan.warnings:
        print("\nWarnings:")
        for warning in plan.warnings:
            print(f"  ! {warning}")


def print_tasks(heading: str, tasks: list[Task]) -> None:
    print(f"\n{heading}")
    if not tasks:
        print("  (none)")
    for task in tasks:
        when = f"{task.preferred_time:%H:%M}" if task.preferred_time else "--:--"
        due = f"  [next occurrence, due {task.due_date:%a %b %d}]" if task.due_date else ""
        print(f"  {when}  {task}{due}")


def print_sort_and_filter_demo(owner: Owner, day: date) -> None:
    scheduler = Scheduler(owner)

    print("\n" + "=" * 60)
    print("Sorting & Filtering Demo")
    print("=" * 60)
    print_tasks("All tasks (insertion order):", owner.get_all_tasks())
    print_tasks("Sorted by time (untimed last, by priority):", scheduler.sort_by_time(owner.get_all_tasks()))

    # Mark a couple of tasks done; each one automatically adds its next occurrence to the pet.
    for task in owner.get_all_tasks():
        if task.title in ("Breakfast", "Thyroid meds"):
            task.mark_complete(day)
    print_tasks("After completing Breakfast and Thyroid meds (all tasks):", owner.get_all_tasks())
    print_tasks("Due today (Scheduler.collect_tasks):", scheduler.sort_by_time(scheduler.collect_tasks(day)))

    print_tasks("Filter: pet_name='Rex'", scheduler.filter_tasks(pet_name="Rex", day=day))
    print_tasks("Filter: pet_name='mia' (case-insensitive)", scheduler.filter_tasks(pet_name="mia", day=day))
    print_tasks("Filter: completed=True", scheduler.filter_tasks(completed=True, day=day))
    print_tasks("Filter: completed=False, sorted by time",
                scheduler.sort_by_time(scheduler.filter_tasks(completed=False, day=day)))
    print_tasks("Filter: pet_name='Rex', completed=False",
                scheduler.filter_tasks(pet_name="Rex", completed=False, day=day))


def print_added(task: Task, warnings: list[str]) -> None:
    print(f"Added {task}: {'' if warnings else 'no conflicts'}")
    for warning in warnings:
        print(f"  ! {warning}")


def print_conflict_check_demo(owner: Owner, day: date) -> None:
    scheduler = Scheduler(owner)
    rex = next(p for p in owner.pets if p.name == "Rex")

    print("\n" + "=" * 60)
    print("Lightweight Conflict Check (warns, never crashes)")
    print("=" * 60)
    for task in (
        Task("Vet call", "other", 20, preferred_time=time(18, 15)),   # overlaps Evening walk
        Task("Nail trim", "grooming", 10, preferred_time=time(12, 0)),  # clear
    ):
        rex.add_task(task)
        print_added(task, scheduler.conflict_warnings(scheduler.collect_tasks(day), focus=task))

    broken = Task("Broken entry", "other", 10)
    broken.preferred_time = "6pm"  # bad data that skipped validation
    rex.add_task(broken)
    print_added(broken, scheduler.conflict_warnings(scheduler.collect_tasks(day), focus=broken))
    rex.remove_task(broken)
    print("Program still running.")


def print_same_time_check(owner: Owner) -> None:
    """Verify the scheduler flags tasks that share the exact same preferred start time."""
    scheduler = Scheduler(owner)
    tasks = owner.get_all_tasks()

    print("\n" + "=" * 60)
    print("Same-Time Conflict Check")
    print("=" * 60)
    same_time = [
        (first, second)
        for first, second in scheduler.find_time_conflicts(tasks)
        if first.preferred_time == second.preferred_time
    ]
    for first, second in same_time:
        print(f"  {first.preferred_time:%H:%M}  {first}  <->  {second}")
    for warning in scheduler.conflict_warnings(tasks):
        print(f"  ! {warning}")

    # Order within a pair doesn't matter, so compare as unordered pairs of titles.
    expected = {frozenset({"Breakfast"}), frozenset({"Thyroid meds", "Eye drops"})}
    found = {frozenset({a.title, b.title}) for a, b in same_time}
    print(f"\nExpected same-time pairs found: {'PASS' if expected <= found else 'FAIL'}")


if __name__ == "__main__":
    today = date.today()
    owner = build_sample_owner()
    print_schedule(owner, today)
    print_same_time_check(owner)
    print_sort_and_filter_demo(owner, today)
    print_conflict_check_demo(owner, today)
