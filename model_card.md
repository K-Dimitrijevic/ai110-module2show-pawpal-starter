# Model Card: PawPal+ AI Care Advisor



## Overview

PawPal+ AI Care Advisor extends my PawPal project, a rule-based daily planner for pet care. The new feature suggests care tasks a pet may be missing, based on its species, life stage and special needs. The suggestions are grounded in a small knowledge base of pet-care guides and explained with citations. The owner approves every suggestion before it becomes part of the schedule.

| | |
|---|---|
| **Feature type** | Retrieval-augmented suggestions plus a reliability harness (validator, evaluation script, confidence scores, logging) |
| **Default suggestion source** | 27 pre-listed answers in `knowledge/answers.json`, each tied to a guide section |
| **Optional suggestion source** | Claude (`claude-opus-5-5` by default) writing suggestions from the retrieved guide sections; needs a paid API key |
| **Retrieval** | BM25 keyword search over 12 hand-written guides (41 sections), filtered by species |
| **Scheduling** | The original PawPal+ rule-based scheduler (unchanged) |

## Intended use

- **For:** a pet owner planning daily care who wants reminders of common care tasks for their pet's life stage or condition, with a source for each one.
- **Not for:** diagnosis, dosing, or replacing a veterinarian. PawPal+ never sets a medication dose, and every suggestion page says the advice is general and not a substitute for a vet.

## Data

- **Care guides** (`knowledge/*.md`): 12 short guides I wrote from general, widely published pet-care advice (senior dogs and cats, puppies, cat and dog diabetes, feline hyperthyroidism, rabbits, medication safety, dental care, meal and exercise timing). They were not reviewed by a vet.
- **Pre-listed answers** (`knowledge/answers.json`): 27 ready-made tasks, each with a reason and the id of the guide section it comes from.
- **No user data** leaves the computer in the default mode. In Claude mode, the pet's profile and the retrieved guide text are sent to the Anthropic API.

## Evaluation results

From `python evaluate_rag.py` (8 test pets, each run twice) and `python -m pytest`:

| Metric | Result |
|---|---|
| Automated tests | 87 / 87 pass |
| Retrieval: expected guide in top 3 | 8 / 8 pets |
| Expected task suggested | 7 / 7 pets that have one |
| Medication suggested for a healthy pet | 0% |
| Consistent across repeated runs | 8 / 8 |
| Average match confidence | 0.76 |
| Planted bad suggestions caught by the validator | 3 / 3 |

These numbers come from test pets, answers and matching rules I wrote myself, so they show the system does what I designed. They don't show the advice is complete or right for every pet.

## Limitations and risks

- **Small, hand-written knowledge base.** 27 answers and 12 guides cover common cases only. A pet with a condition that isn't listed (for example kidney disease) gets only general suggestions, with no warning that its condition isn't covered.
- **Keyword matching misses synonyms.** Special needs are matched by words like "thyroid", "diabet" and "arthritis". "Hyperthyroid" works because it contains "thyroid", but "sugar diabetes" spelled differently, or "CKD", would not match.
- **Not vet-reviewed.** The guides are general advice. A suggestion like "joint supplement" should be checked with a vet before starting.
- **Confidence is rule-based, not measured.** The 0.9 / 0.75 / 0.6 scores describe how specific an answer is, not how likely it is to be right.
- **Optimistic evaluation.** I tuned the retriever and wrote the evaluation pets, so 100% on those 8 pets is expected. Real pets will be messier.
- **Claude mode is less predictable.** Its suggestions vary between runs and weren't measured with a real API key in this project. The validator still rejects anything that cites a missing source or has invalid values.
- **Scheduling quirks.** The demo schedule puts Rex's joint supplement at 07:10, before his 07:15 breakfast, even though the guide says to give it with a meal. The scheduler doesn't know that one task should follow another.

## Future improvements

- Warn the owner when a special need doesn't match any answer, instead of silently giving general care.
- Add synonyms (or embedding search) so "CKD", "hyperthyroid" and "sugar diabetes" match the right guides.
- Have a veterinarian review the guides and answers.
- Run `evaluate_rag.py --claude` with an API key and compare Claude's suggestions with the pre-listed answers.
- Collect real owners' pets as evaluation cases instead of only ones I wrote.

---

## Reflection: how I worked with AI

### How I used AI

I built this project with Claude Code as a coding assistant:

- **Design:** I asked for RAG ideas that fit PawPal+, compared four options (care advisor, cited explanations, a Q&A chat, a safety checker), and chose the care advisor with cited explanations. I also had it draft the Mermaid architecture diagram.
- **Implementation:** it wrote the retriever, the validator, the advisor and explainer, the Streamlit changes, the demo and evaluation scripts, and the tests, and ran the tests after each change.
- **Debugging:** it helped with debugging and looked for issues and helped me ensure I implemented the RAG effectively.


### A suggestion I accepted

**Every suggestion must cite a guide section, and a validator checks it in code.** This turned out to be the most important design decision. It let the system automatically reject a made-up "vitamin injection" that cited a guide that doesn't exist. The same check works whether suggestions come from the pre-listed answers or from Claude, and it is what makes the reliability numbers possible.

I also accepted having the owner approve every suggestion before it becomes a task, because this is pet health and the owner knows things the guides don't.

### Suggestions I rejected or revised

- **Claude-only suggestions → pre-listed answers by default.** The first version needed a paid Anthropic API key for every suggestion. My claude.ai subscription doesn't cover the API, and I didn't want to pay per prompt. I had the system changed so suggestions come from a bank of pre-listed answers tied to the guides, with Claude kept as an option. This made the app free to run, consistent, and fully testable. The trade-off is that it only covers what's in the answer bank.
- **The first architecture diagram was too busy.** It had 16 boxes, logging and fallback arrows. I asked for a simpler one that still shows the components, data flow and where people and tests check the results.
- **The first retrieval query was wrong.** When I tested it with example pets, a puppy got the diabetes guide first and a diabetic cat never got the insulin section. The fix was to give life stage and special needs extra weight, and the evaluation now checks this for 8 pets.

