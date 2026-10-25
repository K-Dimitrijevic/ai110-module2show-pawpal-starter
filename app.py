import json
from datetime import date, time
from pathlib import Path

import streamlit as st
from pawpal_system import Owner, Pet, Priority, Scheduler, Task

CATEGORIES = ["walk", "feeding", "meds", "enrichment", "grooming", "other"]
# session_state is lost when the browser is closed or refreshed, so the owner
# is also saved to this file and reloaded at the start of each new session.
DATA_FILE = Path(__file__).parent / "pawpal_data.json"

CONFLICT_PREFIXES = ("Time conflict", "Couldn't check tasks for time conflicts")


def _task_status(task: Task, day: date) -> str:
    """Describe where a task stands on the given day, e.g. 'due today' or 'done Oct 24'."""
    if task.last_completed is not None:
        return "✅ done" if task.last_completed == day else f"done {task.last_completed:%b %d}"
    if task.is_due_today(day):
        return "due today"
    return f"due {task.due_date:%b %d}"


st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")

# Streamlit reruns this script on every interaction, so the Owner (with its pets
# and tasks) lives in session_state and is only created on the first run.
if "owner" not in st.session_state:
    try:
        saved = Owner.load(DATA_FILE)
    except (json.JSONDecodeError, KeyError, ValueError) as err:
        st.warning(f"Couldn't read saved data ({err}); starting fresh.")
        saved = None
    owner = saved or Owner("Jordan", available_minutes=120, day_start=time(8, 0))
    st.session_state.owner = owner
    st.session_state.owner_name = owner.name
    st.session_state.available_minutes = owner.available_minutes
    st.session_state.day_start = owner.day_start
    st.session_state.max_task_minutes = owner.max_task_minutes or 0
owner: Owner = st.session_state.owner

st.title("🐾 PawPal+")
st.caption("Plan your pets' care for the day based on your time, priorities, and preferences.")

# --- Owner -------------------------------------------------------------------
st.subheader("Owner")
st.text_input("Owner name", key="owner_name")
col1, col2, col3 = st.columns(3)
with col1:
    st.number_input("Minutes available today", min_value=0, max_value=1440, step=15, key="available_minutes")
with col2:
    st.time_input("Day starts at", key="day_start", step=900)
with col3:
    st.number_input("Longest task (min, 0 = no limit)", min_value=0, max_value=240, key="max_task_minutes")

owner.name = st.session_state.owner_name
owner.set_availability(int(st.session_state.available_minutes), st.session_state.day_start)
owner.max_task_minutes = int(st.session_state.max_task_minutes) or None

st.divider()

# --- Pets --------------------------------------------------------------------
st.subheader("Pets")
with st.form("add_pet", clear_on_submit=True):
    col1, col2, col3 = st.columns(3)
    with col1:
        pet_name = st.text_input("Pet name")
    with col2:
        species = st.selectbox("Species", ["dog", "cat", "other"])
    with col3:
        age = st.number_input("Age (years)", min_value=0, max_value=40, value=1)
    special_needs = st.text_input("Special needs (comma-separated, optional)")
    if st.form_submit_button("Add pet"):
        if not pet_name.strip():
            st.error("Please enter a pet name.")
        elif any(p.name.lower() == pet_name.strip().lower() for p in owner.pets):
            st.error(f"You already have a pet named {pet_name.strip()}.")
        else:
            needs = [n.strip() for n in special_needs.split(",") if n.strip()]
            owner.add_pet(Pet(pet_name.strip(), species, int(age), needs))
            st.success(f"Added {pet_name.strip()}.")

if owner.pets:
    for pet in owner.pets:
        st.markdown(f"- {pet.summary()}")
else:
    st.info("No pets yet. Add one above.")

st.divider()

# --- Tasks -------------------------------------------------------------------
st.subheader("Tasks")
if not owner.pets:
    st.caption("Add a pet first, then give it some tasks.")
else:
    with st.form("add_task", clear_on_submit=True):
        pet_choice = st.selectbox("For pet", [p.name for p in owner.pets])
        col1, col2 = st.columns(2)
        with col1:
            title = st.text_input("Task title", value="Morning walk")
            category = st.selectbox("Category", CATEGORIES)
            duration = st.number_input("Duration (minutes)", min_value=1, max_value=240, value=20)
        with col2:
            priority = st.selectbox("Priority", ["low", "medium", "high"], index=2)
            frequency = st.selectbox("Frequency", ["daily", "weekly"])
            is_required = st.checkbox("Required (always schedule, e.g. meds)")
        has_time = st.checkbox("Has a preferred time")
        preferred = st.time_input("Preferred time", value=time(8, 0), step=900)
        if st.form_submit_button("Add task"):
            if not title.strip():
                st.error("Please enter a task title.")
            else:
                pet = next(p for p in owner.pets if p.name == pet_choice)
                task = Task(
                    title.strip(),
                    category,
                    int(duration),
                    Priority.parse(priority),
                    frequency,
                    preferred if has_time else None,
                    is_required,
                )
                pet.add_task(task)
                scheduler = Scheduler(owner)
                clashes = scheduler.conflict_warnings(scheduler.collect_tasks(date.today()), focus=task)
                if clashes:
                    st.warning(
                        f"Added '{task.title}' for {pet.name}, but it overlaps {len(clashes)} other "
                        "task(s). See **Time conflicts** below."
                    )
                else:
                    st.success(f"Added '{task.title}' for {pet.name}.")

    today = date.today()
    scheduler = Scheduler(owner)

    # Check every task due today (not just a new one) so a conflict stays visible until it's fixed.
    conflicts = scheduler.conflict_warnings(scheduler.collect_tasks(today))
    if conflicts:
        st.warning(
            f"**Time conflicts ({len(conflicts)})**: these tasks want overlapping time. "
            "The schedule will shift the later one; change a preferred time to keep both on time.\n\n"
            + "\n".join(f"- {warning}" for warning in conflicts)
        )

    due_today = scheduler.collect_tasks(today)
    if due_today:
        with st.form("complete_task"):
            col1, col2 = st.columns([3, 1], vertical_alignment="bottom")
            with col1:
                labels = {t.id: str(t) for t in scheduler.sort_by_time(due_today)}
                done_id = st.selectbox("Mark a task done", list(labels), format_func=labels.get)
            with col2:
                if st.form_submit_button("Mark done"):
                    done = next(t for t in due_today if t.id == done_id)
                    next_task = done.mark_complete(today)
                    st.session_state.completed_message = (
                        f"Marked '{done.title}' done. Next one is due {next_task.due_date:%A, %B %d}."
                    )
                    st.rerun()
    if "completed_message" in st.session_state:
        st.success(st.session_state.pop("completed_message"))

    col1, col2 = st.columns(2)
    with col1:
        pet_filter = st.selectbox("Show pet", ["All pets"] + [p.name for p in owner.pets])
    with col2:
        status_filter = st.selectbox("Show tasks", ["Due today", "Done today", "All"])

    tasks = scheduler.filter_tasks(
        pet_name=None if pet_filter == "All pets" else pet_filter,
        completed={"Due today": False, "Done today": True, "All": None}[status_filter],
        day=today,
    )
    if status_filter == "Due today":
        tasks = [t for t in tasks if t.is_due_today(today)]

    if tasks:
        st.table(
            [
                {
                    "Time": f"{t.preferred_time:%H:%M}" if t.preferred_time else "flexible",
                    "Pet": t.pet.name,
                    "Task": t.title,
                    "Category": t.category,
                    "Minutes": t.duration_minutes,
                    "Priority": t.priority.name.lower(),
                    "Repeats": t.frequency,
                    "Required": "yes" if t.is_required else "",
                    "Status": _task_status(t, today),
                }
                for t in scheduler.sort_by_time(tasks)
            ],
            hide_index=True,
        )
        st.caption("Sorted by preferred time. Flexible tasks are listed last, highest priority first.")
    elif owner.get_all_tasks():
        st.info("No tasks match these filters.")
    else:
        st.info("No tasks yet. Add one above.")

st.divider()

# --- Schedule ----------------------------------------------------------------
st.subheader("Today's Schedule")
col1, col2 = st.columns(2)
with col1:
    if st.button("Generate schedule", type="primary", disabled=not owner.get_all_tasks()):
        st.session_state.plan = Scheduler(owner).generate_plan()
with col2:
    if st.button("Reset everything"):
        DATA_FILE.unlink(missing_ok=True)
        st.session_state.clear()
        st.rerun()

plan = st.session_state.get("plan")
if plan is not None:
    rows = plan.to_table()
    scheduled = [r for r in rows if r["status"] == "scheduled"]
    skipped = [r for r in rows if r["status"] == "skipped"]

    conflicts = [w for w in plan.warnings if w.startswith(CONFLICT_PREFIXES)]
    moved = [w for w in plan.warnings if "moved from preferred" in w]
    other_warnings = [w for w in plan.warnings if w not in conflicts and w not in moved]

    st.markdown(f"**{plan.plan_date:%A, %B %d}**")
    col1, col2, col3 = st.columns(3)
    col1.metric("Scheduled", len(scheduled), border=True)
    col2.metric("Minutes used", f"{plan.total_minutes_used} / {owner.available_minutes}", border=True)
    col3.metric("Skipped", len(skipped), border=True)

    if scheduled and not plan.warnings and not skipped:
        st.success("Everything fits: no conflicts, and every task is at its preferred time.")
    elif scheduled and not plan.warnings:
        st.success("No conflicts in today's plan.")
    if conflicts:
        st.warning(
            "**Time conflicts**: these tasks want overlapping time, so the later one was "
            "shifted. Change a preferred time if both need to happen on time.\n\n"
            + "\n".join(f"- {warning}" for warning in conflicts)
        )
    for warning in other_warnings:
        st.warning(warning)

    if scheduled:
        st.table(
            [
                {
                    "Time": r["time"],
                    "Pet": r["pet"],
                    "Task": r["task"],
                    "Minutes": r["minutes"],
                    "Priority": r["priority"],
                    "Why": r["why"],
                }
                for r in scheduled
            ],
            hide_index=True,
        )
    else:
        st.info("Nothing could be scheduled. Try adding more available minutes.")
    if moved:
        st.info("**Moved tasks**\n\n" + "\n".join(f"- {warning}" for warning in moved))
    if skipped:
        st.markdown("**Skipped**")
        st.table(
            [{"Pet": r["pet"], "Task": r["task"], "Minutes": r["minutes"], "Why": r["why"]} for r in skipped],
            hide_index=True,
        )
    with st.expander("Full explanation"):
        st.text(plan.explain())
    st.caption("Changed something? Click Generate schedule again to update the plan.")

# Save after every run so pets and tasks survive a browser restart.
owner.save(DATA_FILE)
