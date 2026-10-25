# PawPal+ Project Reflection

## 1. System Design

**a. Initial design**

- Briefly describe your initial UML design.
- What classes did you include, and what responsibilities did you assign to each?

I included a `Task` class, which is for activites that one might do with their pet. A `Pet` class which is the particular animak being cared for, an `Owner` which is obviously the person who shapes the associated plan and a `Scheduler` which houses all the decision-making and collects all the tasks given by an Owner.

**b. Design changes**

- Did your design change during implementation?
- If yes, describe at least one change and why you made it.

Yes. Before writing any scheduling logic, I reviewed the class skeleton and found places where the methods had nothing to work with or no way to pass results along. I changed the design in these ways:

- **Keeping pets and tasks linked:** `Pet.add_task()` and `remove_task()` now set and clear `task.pet`, so a task's pet and the pet's task list can't disagree.
- **Showing warnings in the plan:** `DailyPlan` gained a `warnings` list, so conflicts found by `detect_conflicts()` can be shown to the user.
- **Typed preferences:** I replaced the open-ended `preferences` dict on `Owner` with two fields: `day_start` (when the day begins) and `max_task_minutes`. The scheduler now knows exactly which preferences exist, and `assign_times()` has a time to start from.
- **Tracking when tasks were done:** I replaced the `completed` yes/no flag on `Task` with a `last_completed` date. A simple flag can't tell whether a weekly task is due, and nothing reset it each day. A "twice daily" task is now entered as two separate tasks.
- **Passing data between scheduler steps:** Each `Scheduler` step now takes a list and returns a result instead of sharing a `self.tasks` list. `fit_to_time()` returns both the chosen tasks and the skipped tasks with reasons, and `assign_times()` returns `(start_time, task)` pairs. This makes each step testable on its own and lets `generate_plan()` be run more than once safely. Required tasks are always scheduled, with a warning if they go over the time budget.
- **Safer values:** Priority is now a `Priority` enum (LOW/MEDIUM/HIGH) instead of free text, and `preferred_time` is a real `time` object. This prevents typos and makes times comparable.
- **Smaller fixes:** I renamed `DailyPlan.date` to `plan_date` because it had the same name as the `date` type. I set `Pet` and `Owner` to compare by identity (`eq=False`), so `remove_pet()` can't remove the wrong pet when two have the same details.

I updated the UML diagram to match.

---

## 2. Scheduling Logic and Tradeoffs

**a. Constraints and priorities**

- What constraints does your scheduler consider (for example: time, priority, preferences)?
- How did you decide which constraints mattered most?

**b. Tradeoffs**

- Describe one tradeoff your scheduler makes.
- Why is that tradeoff reasonable for this scenario?

My scheduler warns about time conflicts instead of deciding which task wins. `find_time_conflicts()` checks whether two tasks' preferred time windows overlap (start time plus duration), not just whether they start at exactly the same time. So a 5-minute supplement at 07:45 is flagged against a 07:30–08:00 walk. When it finds a clash, it keeps both tasks, places one right after the other, and adds a warning to the plan, such as "Time conflict, same pet (Mia): Thyroid meds at 08:00-08:05 overlaps Eye drops at 08:00-08:05." It doesn't drop either task or decide which one matters more. If a task has bad data, `conflict_warnings()` returns a warning instead of crashing the program.

This is reasonable because the owner knows things the code doesn't. Two pets can often be fed at the same time, but a dog can't be walked while getting eye drops. Automatically removing a task could mean skipping meds, which is worse than the plan running a few minutes late. The cost is that the owner has to read the warnings and decide, and when a task gets pushed back it can push the tasks after it back too (Thyroid meds moved from 08:00 to 08:05).

I also chose readability over speed in how conflicts are found. The scheduler compares every pair of timed tasks using one overlap rule (`_overlaps()`: A starts before B ends and B starts before A ends), instead of a faster but harder-to-follow sweep. That takes more comparisons as the task list grows, but an owner has around 5–30 tasks a day, so it's still instant and much easier to read and test.

---

## 3. AI Collaboration

**a. How you used AI**

- How did you use AI tools during this project (for example: design brainstorming, debugging, refactoring)?
- What kinds of prompts or questions were most helpful?

I used Claude Code at each stage of the project:

- **Design brainstorming:** I proposed the five classes (`Owner`, `Pet`, `Task`, `Scheduler`, `DailyPlan`) with their attributes and methods, and had Claude turn them into a Mermaid class diagram.
- **Skeleton:** I then had it turn the UML into Python class stubs, using dataclasses for the data classes and checked to make sure everything was coherent.

- **Design review:** I asked it to review its own skeleton for missing relationships and logic bottlenecks. Of which, it found eight issues, and I asked it to make the changes and summarize them.
- **Implementation:** it implemented all the methods, built `main.py` as a terminal testing ground, wrote the first tests, and added docstrings and then I tested accordingly

The most helpful prompts were the ones that asked it to check work rather than just produce it. Some examples are `assign_times()` which had nowhere to store its result, and a yes/no `completed` flag couldn't handle weekly tasks.

**b. Judgment and verification**

- Describe one moment where you did not accept an AI suggestion as-is.
- How did you evaluate or verify what the AI suggested?

I didn't accept the initial skeleton. I had it review first for any issues which led to changes such as a `last_completed` date instead of a `completed` flag, a `Priority` enum, and scheduler steps that pass results to each other instead of sharing one list.

To verify the results:

- **Terminal run:** I had it create `main.py` and run it. The schedule mixed tasks from both pets, kept the 07:00, 07:30, 08:00 and 18:00 preferred times, and skipped the 60-minute hike with the reason "longer than your 45-minute task limit".
- **Unit tests:** I asked for pytest tests that check `mark_complete()` changes a task's status and `add_task()` increases a pet's task count. Both pass.

---

## 4. Testing and Verification

**a. What you tested**

- What behaviors did you test?
- Why were these tests important?

**b. Confidence**

- How confident are you that your scheduler works correctly?
- What edge cases would you test next if you had more time?

---

## 5. Reflection

**a. What went well**

- What part of this project are you most satisfied with?

**b. What you would improve**

- If you had another iteration, what would you improve or redesign?

**c. Key takeaway**

- What is one important thing you learned about designing systems or working with AI on this project?
