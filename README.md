# PawPal+ (Module 2 Project)

You are building **PawPal+**, a Streamlit app that helps a pet owner plan care tasks for their pet.

## Scenario

A busy pet owner needs help staying consistent with pet care. They want an assistant that can:

- Track pet care tasks (walks, feeding, meds, enrichment, grooming, etc.)
- Consider constraints (time available, priority, owner preferences)
- Produce a daily plan and explain why it chose that plan

Your job is to design the system first (UML), then implement the logic in Python, then connect it to the Streamlit UI.

## What you will build

Your final app should:

- Let a user enter basic owner + pet info
- Let a user add/edit tasks (duration + priority at minimum)
- Generate a daily schedule/plan based on constraints and priorities
- Display the plan clearly (and ideally explain the reasoning)
- Include tests for the most important scheduling behaviors

## Getting started

### Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Suggested workflow

1. Read the scenario carefully and identify requirements and edge cases.
2. Draft a UML diagram (classes, attributes, methods, relationships).
3. Convert UML into Python class stubs (no logic yet).
4. Implement scheduling logic in small increments.
5. Add tests to verify key behaviors.
6. Connect your logic to the Streamlit UI in `app.py`.
7. Refine UML so it matches what you actually built.

## 🛠️ Implementation Summary

The scheduling logic lives in `pawpal_system.py`, with a terminal demo in `main.py` (`python main.py`) and tests in `tests/`. The class diagram is in `diagrams/uml.mmd`.

| Class | Responsibility |
|-------|----------------|
| `Task` | One care activity: title, category, duration, `Priority` (low/medium/high), frequency (daily/weekly), optional preferred time, whether it's required, and when it was last completed. Knows whether it is due on a given day and gives a priority score for sorting. |
| `Pet` | A pet's details and its list of tasks. Adding a task links the task back to the pet. |
| `Owner` | The owner's constraints (available minutes, when the day starts, longest task they'll accept) and their pets. `get_all_tasks()` returns every pet's tasks in one list. |
| `Scheduler` | Builds the plan. Gets its tasks only through `Owner.get_all_tasks()`, so it works across all pets. |
| `DailyPlan` | The result: scheduled tasks with start times, skipped tasks with reasons, warnings, and a readable explanation. `to_table()` returns rows for display. |

**How the classes work together:** `Scheduler.generate_plan(day)` runs five steps, each taking the previous step's output:

1. **Collect:** tasks due today, from all pets, via `owner.get_all_tasks()`.
2. **Sort:** required tasks first, then by priority score, then shortest first.
3. **Fit:** keep tasks while time remains. Required tasks are always kept, and anything longer than the owner's task limit is skipped.
4. **Assign times:** start at the owner's `day_start`, place tasks with a preferred time at that time (or as soon after as possible), and fill gaps with flexible tasks.
5. **Check:** warn about an over-budget plan, moved preferred times, overlaps, or tasks running past midnight.

Every scheduled or skipped task is recorded in the `DailyPlan` with a plain-language reason, which is how the app explains its choices.

## 🖥️ Sample Output

Paste a sample of your app's CLI or Streamlit output here so a reader can see what a generated plan looks like:

```
# e.g.:
# Daily plan for Biscuit (Golden Retriever):
#   08:00 — Morning walk (30 min) [priority: high]
#   09:00 — Feeding (10 min) [priority: high]
#   ...
```

```
============================================================
Today's Schedule for Jordan - Sunday, October 25, 2026
============================================================
Pets:
  - Rex (dog, 4 yr) - 4 task(s)
  - Mia (cat, 9 yr) - 3 task(s); needs: senior, thyroid condition

Time   Pet   Task             Min  Priority Why
------------------------------------------------------------
07:00  Rex   Breakfast         10  high     required, at your preferred time
07:10  Mia   Puzzle feeder     15  low      low priority, fits in your available time
07:30  Rex   Morning walk      30  high     high priority, at your preferred time
08:00  Mia   Thyroid meds       5  high     required, at your preferred time
08:05  Mia   Brushing          15  low      low priority, fits in your available time
18:00  Rex   Evening walk      30  medium   medium priority, at your preferred time
------------------------------------------------------------
Total: 105 of 120 available minutes

Skipped:
  - Rex: Long hike (60 min, medium): longer than your 45-minute task limit 
  ```

## 🧪 Testing PawPal+

```bash
# Run the full test suite:
pytest

# Run with coverage:
pytest --cov
```

Sample test output:

```
# Paste your pytest output here
```

## 📐 Smarter Scheduling

> Fill in once you've implemented scheduling logic.

| Feature | Method(s) | Notes |
|---------|-----------|-------|
| Task sorting | | e.g., by priority, duration |
| Filtering | | e.g., skip tasks if time runs out |
| Conflict handling | | e.g., overlapping time slots |
| Recurring tasks | | e.g., daily vs. weekly |

## 📸 Demo Walkthrough

Describe your app in numbered steps so a reader can follow along without watching a video:

1. <!-- Describe this step -->
2. <!-- Describe this step -->
3. <!-- Describe this step -->
4. <!-- Describe this step -->
5. <!-- Add more steps as needed -->

**Screenshot or video** *(optional)*: <!-- Insert a screenshot or link to a demo video here -->
