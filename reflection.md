# PawPal+ Project Reflection

## 1. System Design

**a. Initial design**

- Briefly describe your initial UML design.
- What classes did you include, and what responsibilities did you assign to each?

I included a `Task` class, which is for activities that one might do with their pet. A `Pet` class which is the particular animal being cared for, an `Owner` which is obviously the person who shapes the associated plan and a `Scheduler` which houses all the decision-making and collects all the tasks given by an Owner.

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

Later on, recurring tasks added a `due_date` and a `next_occurrence()` method to `Task`, and the scheduler picked up sorting, filtering and conflict-checking methods. I updated the UML diagram to match (`diagrams/uml_final.mmd`).

---

## 2. Scheduling Logic and Tradeoffs

**a. Constraints and priorities**

- What constraints does your scheduler consider (for example: time, priority, preferences)?
- How did you decide which constraints mattered most?

The scheduler looks at:

- whether a task is required
- its priority
- how many minutes the owner has
- the longest task the owner is willing to do
- what time the day starts
- any preferred time a task has

I ranked them by what would hurt the pet most if it got missed. Required tasks like meds come first and always get scheduled, even if that goes over the time budget. A late or skipped walk isn't great, but missed meds can actually be a problem. After that comes priority, then the time budget. Preferred times come last: the scheduler tries to hit them, but it will push a task back a few minutes rather than drop it.

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
- **Implementation:** it implemented all the methods, built `main.py` as a terminal testing ground, wrote the first tests, and added docstrings and then I tested accordingly.
- **Testing, UI and docs:** I asked it which edge cases mattered most, then had it write the tests, hook the scheduler's sorting, filtering and conflict warnings into the Streamlit app, and update the UML and README to match the final code.

The most helpful prompts were the ones that asked it to check work rather than just produce it. Some examples are `assign_times()` which had nowhere to store its result, and a yes/no `completed` flag couldn't handle weekly tasks. Asking "does this project meet the rubric?" was also useful, because it caught things I'd missed, like the UML being out of date and the README showing old output.

**b. Judgment and verification**

- Describe one moment where you did not accept an AI suggestion as-is.
- How did you evaluate or verify what the AI suggested?

I didn't accept the initial skeleton. I had it review first for any issues which led to changes such as a `last_completed` date instead of a `completed` flag, a `Priority` enum, and scheduler steps that pass results to each other instead of sharing one list.

To verify the results:

- **Terminal run:** I had it create `main.py` and run it. The schedule mixed tasks from both pets, kept the 07:00, 07:30, 08:00 and 18:00 preferred times, and skipped the 60-minute hike with the reason "longer than your 45-minute task limit".
- **Unit tests:** I started with two tests (`mark_complete()` changes a task's status, and `add_task()` increases a pet's task count). The suite is now 36 tests, and they all pass.
- **The app itself:** after the UI changes, the app was run with sample data to check that the filters, the "Mark done" button and the conflict warnings actually showed up the way they should.

**c. AI strategy**

*Which AI coding assistant features were most effective for building your scheduler?*

The most useful thing was that Claude could run the code when needed. It ran `main.py` and pytest after each change, so I saw the real output instead of taking its word that something worked and hoping it didn't hallucinate. It was also good at reading the whole project at once. When I asked for edge cases, it pointed to specific lines in `pawpal_system.py`, like the `break` in `assign_times()` that drops every task after midnight instead of giving me a generic list and forcing me to spend an hour or so evaluating myself.

*Give one example of an AI suggestion you rejected or modified to keep your system design clean.*

The first skeleton gave `Owner` an open-ended `preferences` dictionary and gave `Task` a simple `completed` true/false flag. Both looked flexible, but they would have made the scheduler messier. With a dictionary, the scheduler has to guess which keys exist. A true/false flag can't tell you whether a weekly task is due again. I had it swap the dictionary for two real fields, `day_start` and `max_task_minutes`, and replace the flag with a `last_completed` date. That one change is what made recurring tasks possible later without adding special cases.

*How did using separate chat sessions for different phases help you stay organized?*

I kept design and the class skeleton in one chat, and scheduling logic, testing, UI and docs in others as described in the steps. Each chat stayed focused on one job so the AI wouldn't mix old design ideas into my new code. It also forced me to restate where the project was at the start of each phase. When a new chat started, it read the current files instead of relying on an older plan that might be out of date.

*Summarize what you learned about being the "lead architect" when collaborating with powerful AI tools.*

The AI can write a lot of correct-looking code very efficiently, so my job was a lot of peer-reviewing in truth. I had to know what the system was supposed to do, ask it to review its own work, and start from scratch when the AI result wasn't what i desired. Some decisions it couldn't make for me. One example is whether a weekly task done late should count its next due date from when it was done or when it was due. The AI could point the question out, but the answer depends on what I'd think a pet owner would expect. I also learned to verify everything by running it, since the AI explains its mistakes just as confidently as its correct answers, which can be really unhelpful.

---

## 4. Testing and Verification

**a. What you tested**

- What behaviors did you test?
- Why were these tests important?

I tested the things a pet owner would notice if they broke:

- **Sorting:** tasks come out in time order with untimed tasks last.
- **Required tasks:** they're never dropped, even when there isn't enough time.
- **Priority:** higher-priority tasks get the time first when the budget is tight.
- **Preferred times:** they're honored, or the task is pushed back if something's in the way.
- **Recurrence:** daily and weekly tasks come back on the right day, including across months, years and leap days.
- **Conflicts:** they're flagged for the same pet and across different pets.

I also tested empty cases, like an owner with no pets or a pet with no tasks, so the app doesn't crash on a fresh start. These matter because a wrong schedule could mean a pet misses meds or a meal, and that's the one thing this app can't get wrong.

**b. Confidence**

- How confident are you that your scheduler works correctly?
- What edge cases would you test next if you had more time?

About a 4 out of 5. All 36 tests pass and the demo output looks right, but there are a few cases I know behave in a way I haven't fully decided on:

- **Overlaps across midnight:** a task at 23:50 that runs 30 minutes isn't flagged against one at 00:05.
- **Short tasks waiting behind long ones:** a short flexible task waits behind a long one, even if it could fit in an earlier gap.
- **Late completions:** a weekly task done late counts its next due date from the day it was done.

I'd test and decide on those next.

---

## 5. Reflection

**a. What went well**

- What part of this project are you most satisfied with?

I'm happiest with how the scheduler is split into small steps (collect, sort, fit, assign times, check). Each step takes a list and returns a result, so I could test each one by itself, and adding conflict warnings later didn't mean rewriting anything. I also like that every scheduled or skipped task comes with a plain reason, so the plan explains itself.

**b. What you would improve**

- If you had another iteration, what would you improve or redesign?

I'd make flexible tasks smarter about filling gaps, so a short task can slot in before a fixed one even if a longer task is ahead of it in priority. I'd also add a way to edit and delete tasks in the app (the `edit_task()` method exists but the UI doesn't use it yet), and clean up old completed copies of recurring tasks so the "All" list doesn't keep growing.

**c. Key takeaway**

- What is one important thing you learned about designing systems or working with AI on this project?

Getting the design right early saves a lot of work later. The changes I made after the skeleton review, like the `last_completed` date and the scheduler steps passing results to each other, are the reason features like recurring tasks and conflict warnings worked cleanly. Including AI in the workflow made that even more the case. It builds whatever design you give it, so the design has to be a good one which ultimately depends on my, the 'lead architect' so to speak.
