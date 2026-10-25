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


def _plan_owner(available_minutes=60, day_start=time(8, 0), **kwargs):
    owner = Owner("Jordan", available_minutes=available_minutes, day_start=day_start, **kwargs)
    rex = Pet("Rex", "dog")
    owner.add_pet(rex)
    return owner, rex


def _fit(owner, tasks):
    scheduler = Scheduler(owner)
    return scheduler.fit_to_time(scheduler.sort_by_priority(tasks))


def test_sort_by_time_keeps_ties_in_order_and_puts_midnight_first():
    walk_a = Task("Walk A", "walk", 30, preferred_time=time(7, 0))
    walk_b = Task("Walk B", "walk", 30, preferred_time=time(7, 0))
    night_meds = Task("Night meds", "meds", 5, preferred_time=time(0, 0))
    puzzle = Task("Puzzle", "enrichment", 15)
    scheduler = Scheduler(Owner("Jordan"))

    assert scheduler.sort_by_time([walk_a, puzzle, walk_b, night_meds]) == [night_meds, walk_a, walk_b, puzzle]
    assert scheduler.sort_by_time([walk_b, walk_a]) == [walk_b, walk_a]
    assert scheduler.sort_by_time([]) == []


def test_sort_by_priority_breaks_ties_by_category_then_shortest():
    walk = Task("Walk", "walk", 10)
    feed = Task("Feed", "feeding", 10)
    meds = Task("Meds", "meds", 10)
    long_groom = Task("Long groom", "grooming", 15)
    short_groom = Task("Short groom", "grooming", 5)

    ordered = Scheduler(Owner("Jordan")).sort_by_priority([walk, feed, meds, long_groom, short_groom])

    assert ordered == [meds, feed, walk, short_groom, long_groom]


def test_fit_to_time_keeps_required_tasks_over_budget():
    owner, rex = _plan_owner(available_minutes=30)
    meds = Task("Meds", "meds", 20, is_required=True)
    vet = Task("Vet visit", "other", 40, is_required=True)
    walk = Task("Walk", "walk", 10)
    for task in (walk, meds, vet):
        rex.add_task(task)

    chosen, skipped = _fit(owner, rex.tasks)

    assert chosen == [meds, vet]
    assert skipped == [(walk, "needs 10 min, only 0 min left")]
    plan = Scheduler(owner).generate_plan(date(2026, 10, 25))
    assert "Required tasks need 60 min but only 30 min are available." in plan.warnings


def test_fit_to_time_gives_time_to_higher_priority_first():
    owner, _ = _plan_owner(available_minutes=30)
    puzzle = Task("Puzzle", "enrichment", 20, Priority.LOW)
    walk = Task("Walk", "walk", 20, Priority.HIGH)

    chosen, skipped = _fit(owner, [puzzle, walk])

    assert chosen == [walk]
    assert skipped == [(puzzle, "needs 20 min, only 10 min left")]


def test_fit_to_time_keeps_filling_after_a_skip_up_to_exact_budget():
    owner, _ = _plan_owner(available_minutes=30)
    walk = Task("Walk", "walk", 25, Priority.HIGH)
    grooming = Task("Grooming", "grooming", 20)
    treat = Task("Treat", "feeding", 5, Priority.LOW)

    chosen, skipped = _fit(owner, [grooming, treat, walk])

    assert chosen == [walk, treat]  # 25 + 5 fills the 30 min exactly
    assert skipped == [(grooming, "needs 20 min, only 5 min left")]


def test_fit_to_time_skips_long_tasks_unless_required():
    owner, _ = _plan_owner(available_minutes=200, max_task_minutes=30)
    long_walk = Task("Long walk", "walk", 60, Priority.HIGH)
    vet = Task("Vet visit", "other", 45, is_required=True)
    breakfast = Task("Breakfast", "feeding", 10)

    chosen, skipped = _fit(owner, [long_walk, vet, breakfast])

    assert chosen == [vet, breakfast]
    assert skipped == [(long_walk, "longer than your 30-minute task limit")]


def test_fit_to_time_with_zero_budget_keeps_only_required():
    owner, _ = _plan_owner(available_minutes=0)
    meds = Task("Meds", "meds", 5, is_required=True)
    walk = Task("Walk", "walk", 10)

    chosen, skipped = _fit(owner, [walk, meds])

    assert chosen == [meds]
    assert skipped == [(walk, "needs 10 min, only 0 min left")]


def test_assign_times_honors_preferred_time_and_fills_gap_before_it():
    owner, _ = _plan_owner()
    walk = Task("Walk", "walk", 30)
    meds = Task("Meds", "meds", 5, preferred_time=time(9, 0))

    assert Scheduler(owner).assign_times([walk, meds]) == [(time(8, 0), walk), (time(9, 0), meds)]


def test_assign_times_puts_flexible_task_after_fixed_when_gap_is_too_small():
    owner, _ = _plan_owner()
    walk = Task("Walk", "walk", 30)
    meds = Task("Meds", "meds", 5, preferred_time=time(8, 20))

    assert Scheduler(owner).assign_times([walk, meds]) == [(time(8, 20), meds), (time(8, 25), walk)]


def test_assign_times_keeps_flexible_tasks_in_priority_order():
    # A shorter, lower-priority task could fill the 30-min gap, but flexible
    # tasks are placed strictly in the order given, so it waits its turn.
    owner, _ = _plan_owner()
    walk = Task("Walk", "walk", 60, Priority.HIGH)
    treat = Task("Treat", "feeding", 10, Priority.LOW)
    meds = Task("Meds", "meds", 5, preferred_time=time(8, 30))

    assert Scheduler(owner).assign_times([walk, treat, meds]) == [
        (time(8, 30), meds),
        (time(8, 35), walk),
        (time(9, 35), treat),
    ]


def test_assign_times_moves_task_preferred_before_day_start():
    owner, _ = _plan_owner()
    meds = Task("Meds", "meds", 5, preferred_time=time(7, 0))

    assert Scheduler(owner).assign_times([meds]) == [(time(8, 0), meds)]


def test_generate_plan_shifts_second_task_with_same_preferred_time():
    owner, rex = _plan_owner()
    meds = Task("Meds", "meds", 5, preferred_time=time(8, 30))
    breakfast = Task("Breakfast", "feeding", 10, preferred_time=time(8, 30))
    rex.add_task(breakfast)
    rex.add_task(meds)

    plan = Scheduler(owner).generate_plan(date(2026, 10, 25))

    assert plan.scheduled == [(time(8, 30), meds), (time(8, 35), breakfast)]
    assert plan.reasons[breakfast.id] == "medium priority, as close to 08:30 as possible"
    assert "Rex: Breakfast (10 min, medium) moved from preferred 08:30 to 08:35." in plan.warnings


def test_generate_plan_drops_tasks_that_would_start_after_midnight():
    owner, rex = _plan_owner(available_minutes=200, day_start=time(23, 0))
    walk = Task("Evening walk", "walk", 50, Priority.HIGH)
    dinner = Task("Dinner", "feeding", 20)
    treat = Task("Treat", "feeding", 5, Priority.LOW)
    brushing = Task("Brushing", "grooming", 10, Priority.LOW)
    for task in (brushing, treat, dinner, walk):
        rex.add_task(task)

    plan = Scheduler(owner).generate_plan(date(2026, 10, 25))

    assert plan.scheduled == [(time(23, 0), walk), (time(23, 50), dinner)]
    assert plan.skipped == [(treat, "no time left in the day"), (brushing, "no time left in the day")]
    assert "Rex: Dinner (20 min, medium) runs past midnight." in plan.warnings


def test_generate_plan_is_empty_without_due_tasks():
    day = date(2026, 10, 25)
    no_pets = Owner("Jordan", available_minutes=60)
    no_tasks, _ = _plan_owner()
    all_done, rex = _plan_owner()
    walk = Task("Walk", "walk", 30)
    rex.add_task(walk)
    walk.mark_complete(day)

    for owner in (no_pets, no_tasks, all_done):
        plan = Scheduler(owner).generate_plan(day)
        assert (plan.scheduled, plan.skipped, plan.warnings) == ([], [], [])
    assert Scheduler(no_pets).assign_times([]) == []


def test_next_occurrence_crosses_month_year_and_leap_day():
    daily = Task("Walk", "walk", 30)
    weekly = Task("Brushing", "grooming", 15, frequency="weekly")

    assert daily.next_occurrence(date(2026, 12, 31)).due_date == date(2027, 1, 1)
    assert weekly.next_occurrence(date(2028, 2, 25)).due_date == date(2028, 3, 3)  # leap year
    assert weekly.next_occurrence(date(2027, 2, 25)).due_date == date(2027, 3, 4)


def test_late_completion_counts_next_due_date_from_completion_day():
    mia = Pet("Mia", "cat")
    brushing = Task("Brushing", "grooming", 15, frequency="weekly", is_required=True, due_date=date(2026, 10, 1))
    mia.add_task(brushing)
    assert brushing.is_due_today(date(2026, 10, 10))  # missed, still due

    next_brushing = brushing.mark_complete(date(2026, 10, 10))

    assert next_brushing.due_date == date(2026, 10, 17)
    assert next_brushing.is_required


def test_find_time_conflicts_flags_duplicate_times_for_same_pet():
    owner, rex, _ = _conflict_owner()
    meds = Task("Meds", "meds", 5, preferred_time=time(8, 0))
    breakfast = Task("Breakfast", "feeding", 10, preferred_time=time(8, 0))
    walk = Task("Walk", "walk", 30, preferred_time=time(8, 0))
    for task in (walk, breakfast, meds):
        rex.add_task(task)
    scheduler = Scheduler(owner)

    assert scheduler.find_time_conflicts(rex.tasks) == [(meds, breakfast), (meds, walk), (breakfast, walk)]
    warnings = scheduler.conflict_warnings(rex.tasks)
    assert len(warnings) == 3
    assert all("same pet (Rex)" in w for w in warnings)
