"""Tests for the core PawPal+ classes."""

from datetime import date, time, timedelta

from pawpal_system import Owner, Pet, Priority, Scheduler, Task


def test_mark_complete_changes_task_status():
    task = Task("Morning walk", "walk", 30)
    today = date(2026, 10, 25)
    assert not task.is_completed(today)
    assert task.is_due_today(today)

    task.mark_complete(today)

    assert task.is_completed(today)
    assert task.last_completed == today
    assert not task.is_due_today(today)


def test_add_task_increases_pet_task_count():
    pet = Pet("Rex", "dog")
    assert len(pet.tasks) == 0

    task = Task("Breakfast", "feeding", 10)
    pet.add_task(task)

    assert len(pet.tasks) == 1
    assert task in pet.tasks
    assert task.pet is pet


def test_owner_save_and_load_round_trip(tmp_path):
    owner = Owner("Jordan", available_minutes=90, day_start=time(7, 0), max_task_minutes=45)
    pet = Pet("Mia", "cat", age=9, special_needs=["senior"])
    owner.add_pet(pet)
    task = Task("Meds", "meds", 5, Priority.HIGH, preferred_time=time(8, 0), is_required=True)
    pet.add_task(task)
    task.mark_complete(date(2026, 10, 25))

    path = tmp_path / "owner.json"
    owner.save(path)
    loaded = Owner.load(path)

    assert loaded.to_dict() == owner.to_dict()
    loaded_task = loaded.pets[0].tasks[0]
    assert loaded_task.pet is loaded.pets[0]
    assert loaded_task.priority is Priority.HIGH
    assert loaded_task.last_completed == date(2026, 10, 25)
    assert loaded.pets[0].tasks[1].due_date == date(2026, 10, 26)


def test_load_missing_file_returns_none(tmp_path):
    assert Owner.load(tmp_path / "missing.json") is None


def test_sort_by_time_orders_by_preferred_time_with_untimed_last():
    dinner = Task("Dinner", "feeding", 10, preferred_time=time(18, 0))
    meds = Task("Meds", "meds", 5, preferred_time=time(8, 0))
    puzzle = Task("Puzzle", "enrichment", 15, Priority.LOW)
    brushing = Task("Brushing", "grooming", 15, Priority.HIGH)
    scheduler = Scheduler(Owner("Jordan"))

    ordered = scheduler.sort_by_time([dinner, puzzle, meds, brushing])

    assert [t.title for t in ordered] == ["Meds", "Dinner", "Brushing", "Puzzle"]


def test_filter_tasks_by_pet_name_and_completion():
    today = date(2026, 10, 25)
    owner = Owner("Jordan")
    rex, mia = Pet("Rex", "dog"), Pet("Mia", "cat")
    owner.add_pet(rex)
    owner.add_pet(mia)
    walk = Task("Walk", "walk", 30)
    breakfast = Task("Breakfast", "feeding", 10)
    meds = Task("Meds", "meds", 5)
    rex.add_task(walk)
    rex.add_task(breakfast)
    mia.add_task(meds)
    next_walk = walk.mark_complete(today)
    scheduler = Scheduler(owner)

    assert scheduler.filter_tasks(pet_name="rex", day=today) == [walk, breakfast, next_walk]
    assert scheduler.filter_tasks(completed=True, day=today) == [walk]
    assert scheduler.filter_tasks(completed=False, day=today) == [breakfast, next_walk, meds]
    assert scheduler.filter_tasks(pet_name="Rex", completed=False, day=today) == [breakfast, next_walk]
    assert scheduler.filter_tasks(pet_name="Nobody", day=today) == []
    assert len(scheduler.filter_tasks(day=today)) == 4


def test_completing_daily_task_creates_next_occurrence_tomorrow():
    today = date(2026, 10, 25)
    rex = Pet("Rex", "dog")
    walk = Task("Walk", "walk", 30, Priority.HIGH, preferred_time=time(7, 30))
    rex.add_task(walk)

    next_walk = walk.mark_complete(today)

    assert rex.tasks == [walk, next_walk]
    assert next_walk.pet is rex
    assert next_walk.id != walk.id
    assert next_walk.due_date == today + timedelta(days=1)
    assert next_walk.last_completed is None
    assert (next_walk.title, next_walk.priority, next_walk.preferred_time) == ("Walk", Priority.HIGH, time(7, 30))
    assert not walk.is_due_today(today + timedelta(days=1))
    assert not next_walk.is_due_today(today)
    assert next_walk.is_due_today(today + timedelta(days=1))


def test_completing_weekly_task_creates_next_occurrence_in_seven_days():
    today = date(2026, 10, 25)
    mia = Pet("Mia", "cat")
    brushing = Task("Brushing", "grooming", 15, frequency="weekly")
    mia.add_task(brushing)

    next_brushing = brushing.mark_complete(today)

    assert next_brushing.due_date == date(2026, 11, 1)
    assert not next_brushing.is_due_today(date(2026, 10, 31))
    assert next_brushing.is_due_today(date(2026, 11, 1))
    assert next_brushing.is_due_today(date(2026, 11, 3))  # still due if overdue


def test_completing_twice_does_not_duplicate():
    rex = Pet("Rex", "dog")
    walk = Task("Walk", "walk", 30)
    rex.add_task(walk)

    walk.mark_complete(date(2026, 10, 25))
    assert walk.mark_complete(date(2026, 10, 25)) is None
    assert len(rex.tasks) == 2


def test_scheduler_plans_next_occurrence_only_when_due():
    today = date(2026, 10, 25)
    owner = Owner("Jordan", available_minutes=60)
    rex = Pet("Rex", "dog")
    owner.add_pet(rex)
    walk = Task("Walk", "walk", 30)
    rex.add_task(walk)
    next_walk = walk.mark_complete(today)
    scheduler = Scheduler(owner)

    assert scheduler.generate_plan(today).scheduled == []
    tomorrow_plan = scheduler.generate_plan(today + timedelta(days=1))
    assert [task for _, task in tomorrow_plan.scheduled] == [next_walk]


def _conflict_owner():
    owner = Owner("Jordan", available_minutes=120, day_start=time(7, 0))
    rex, mia = Pet("Rex", "dog"), Pet("Mia", "cat")
    owner.add_pet(rex)
    owner.add_pet(mia)
    return owner, rex, mia


def test_find_time_conflicts_same_pet():
    owner, rex, _ = _conflict_owner()
    walk = Task("Walk", "walk", 30, preferred_time=time(7, 30))
    meds = Task("Meds", "meds", 5, preferred_time=time(7, 45))
    rex.add_task(walk)
    rex.add_task(meds)
    scheduler = Scheduler(owner)

    assert scheduler.find_time_conflicts([meds, walk]) == [(walk, meds)]
    [warning] = scheduler.conflict_warnings([meds, walk])
    assert "same pet (Rex)" in warning
    assert "07:30-08:00" in warning and "07:45-07:50" in warning


def test_find_time_conflicts_different_pets_same_start():
    owner, rex, mia = _conflict_owner()
    rex_breakfast = Task("Breakfast", "feeding", 10, preferred_time=time(7, 0))
    mia_breakfast = Task("Breakfast", "feeding", 5, preferred_time=time(7, 0))
    rex.add_task(rex_breakfast)
    mia.add_task(mia_breakfast)
    scheduler = Scheduler(owner)

    assert len(scheduler.find_time_conflicts([rex_breakfast, mia_breakfast])) == 1
    assert "different pets" in scheduler.conflict_warnings([rex_breakfast, mia_breakfast])[0]


def test_back_to_back_and_untimed_tasks_do_not_conflict():
    owner, rex, mia = _conflict_owner()
    breakfast = Task("Breakfast", "feeding", 30, preferred_time=time(7, 0))
    walk = Task("Walk", "walk", 30, preferred_time=time(7, 30))
    puzzle = Task("Puzzle", "enrichment", 15)
    rex.add_task(breakfast)
    rex.add_task(walk)
    mia.add_task(puzzle)

    assert Scheduler(owner).find_time_conflicts([breakfast, walk, puzzle]) == []


def test_find_time_conflicts_reports_every_overlapping_pair():
    owner, rex, mia = _conflict_owner()
    long_walk = Task("Long walk", "walk", 60, preferred_time=time(7, 0))
    meds = Task("Meds", "meds", 5, preferred_time=time(7, 10))
    feed = Task("Feed", "feeding", 5, preferred_time=time(7, 30))
    rex.add_task(long_walk)
    mia.add_task(meds)
    mia.add_task(feed)

    assert Scheduler(owner).find_time_conflicts([feed, meds, long_walk]) == [
        (long_walk, meds),
        (long_walk, feed),
    ]


def test_generate_plan_warns_about_time_conflicts():
    owner, rex, mia = _conflict_owner()
    rex.add_task(Task("Breakfast", "feeding", 10, preferred_time=time(7, 0)))
    mia.add_task(Task("Breakfast", "feeding", 5, preferred_time=time(7, 0)))

    plan = Scheduler(owner).generate_plan(date(2026, 10, 25))

    assert any(w.startswith("Time conflict, different pets") for w in plan.warnings)
    assert len(plan.scheduled) == 2  # the scheduler still resolves it by shifting one


def _focus_warnings(owner, task, day=date(2026, 10, 25)):
    scheduler = Scheduler(owner)
    return scheduler.conflict_warnings(scheduler.collect_tasks(day), focus=task)


def test_focus_warnings_empty_when_clear():
    owner, rex, mia = _conflict_owner()
    rex.add_task(Task("Walk", "walk", 30, preferred_time=time(7, 30)))
    meds = Task("Meds", "meds", 5, preferred_time=time(8, 0))
    untimed = Task("Puzzle", "enrichment", 15)
    mia.add_task(meds)
    mia.add_task(untimed)

    assert _focus_warnings(owner, meds) == []
    assert _focus_warnings(owner, untimed) == []


def test_focus_warnings_list_only_clashes_with_focus_task():
    owner, rex, mia = _conflict_owner()
    rex.add_task(Task("Walk", "walk", 30, preferred_time=time(7, 30)))
    mia.add_task(Task("Feed", "feeding", 5, preferred_time=time(7, 40)))
    vet_call = Task("Vet call", "other", 20, preferred_time=time(7, 35))
    rex.add_task(vet_call)

    warnings = _focus_warnings(owner, vet_call)

    # Walk/Feed also overlap each other, but only Vet call's clashes are reported.
    assert len(warnings) == 2
    assert all("Rex: Vet call" in w for w in warnings)
    assert any("same pet (Rex)" in w and "Rex: Walk" in w for w in warnings)
    assert any("different pets" in w and "Mia: Feed" in w for w in warnings)


def test_focus_warnings_ignore_completed_tasks():
    owner, rex, _ = _conflict_owner()
    walk = Task("Walk", "walk", 30, preferred_time=time(7, 30))
    rex.add_task(walk)
    walk.mark_complete(date(2026, 10, 25))
    meds = Task("Meds", "meds", 5, preferred_time=time(7, 45))
    rex.add_task(meds)

    assert _focus_warnings(owner, meds) == []


def test_conflict_warnings_warn_instead_of_crashing_on_bad_data():
    owner, rex, _ = _conflict_owner()
    rex.add_task(Task("Walk", "walk", 30, preferred_time=time(7, 30)))
    broken = Task("Broken", "other", 10)
    broken.preferred_time = "7:30am"  # bypasses validation, as a bad edit might
    rex.add_task(broken)

    for warnings in (_focus_warnings(owner, broken), Scheduler(owner).conflict_warnings(rex.tasks)):
        [warning] = warnings
        assert warning.startswith("Couldn't check tasks for time conflicts")
