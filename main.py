"""Temporary testing ground: build a sample owner and print today's schedule."""

from datetime import date, time

from pawpal_system import Owner, Pet, Priority, Scheduler, Task


def build_sample_owner() -> Owner:
    owner = Owner("Jordan", available_minutes=120, day_start=time(7, 0), max_task_minutes=45)

    rex = Pet("Rex", "dog", age=4)
    mia = Pet("Mia", "cat", age=9, special_needs=["senior", "thyroid condition"])
    owner.add_pet(rex)
    owner.add_pet(mia)

    rex.add_task(Task("Breakfast", "feeding", 10, Priority.HIGH, preferred_time=time(7, 0), is_required=True))
    rex.add_task(Task("Morning walk", "walk", 30, Priority.HIGH, preferred_time=time(7, 30)))
    rex.add_task(Task("Evening walk", "walk", 30, Priority.MEDIUM, preferred_time=time(18, 0)))
    rex.add_task(Task("Long hike", "walk", 60, Priority.MEDIUM))
    mia.add_task(Task("Thyroid meds", "meds", 5, Priority.HIGH, preferred_time=time(8, 0), is_required=True))
    mia.add_task(Task("Puzzle feeder", "enrichment", 15, Priority.LOW))
    mia.add_task(Task("Brushing", "grooming", 15, Priority.LOW, frequency="weekly"))

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


if __name__ == "__main__":
    print_schedule(build_sample_owner(), date.today())
