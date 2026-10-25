"""Tests for the core PawPal+ classes."""

from datetime import date

from pawpal_system import Pet, Task


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
