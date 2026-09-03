# Question Curation Brief — VitalGuard / "The Gate"
**For: Adesh.** Paste everything below the line into ChatGPT/Claude/Gemini as a single message.
Return the JSON file to Abhi. Do not reformat it by hand — the game parses it directly.

---

You are writing the question set for a **stress-response experiment** built into a game.

## What the system actually is (read this, it changes what a good question looks like)

A person wears a biosignal band (heart rate, skin conductance, motion) and plays a short,
grim, timed game. The system measures **how their body responds under pressure and how fast
they recover** — always against *their own* baseline, never against other people. It is a
self-knowledge / composure-training tool. It is **not** a hiring or screening tool.

Therefore: **the questions are the stressor, not the assessment.** Their job is to make the
person's pulse and skin conductance move. A question that is merely *difficult* fails. A
question that makes someone hesitate, second-guess, and commit anyway, succeeds.

## What you must produce

**Exactly 20 questions**, split into two kinds:

### Kind A — `"general"` — 13 questions, and these DO have a correct answer
Hard, but objectively answerable by a smart person with no specialist training.
The stress comes from **four plausible options and a short clock**, not from ambiguity.

The bar: *a smart person should be able to build an argument for at least three of the four
options before deciding.* Every distractor must be a trap someone would actually fall into —
a common misconception, an off-by-one, a plausible-but-wrong inference, a reversed causality.

Draw from: probability and base rates, logical deduction, sequence completion, reading a
short scenario for the load-bearing fact, estimation under incomplete information, spotting
the flaw in a stated argument. **No outside knowledge.** No trivia. No wordplay or trick
phrasing — if the "gotcha" is that we hid a word, it is a bad question.

### Kind B — `"career"` — 7 questions, and these have NO correct answer
Genuine dilemmas from working life. All four options must be defensible, and every one must
cost the person something real. Set `"answer": null`.

The bar: *a thoughtful person should be uncomfortable choosing, and should be able to explain
why the option they rejected was also right.* No option may be a strawman, and none may be
the obvious "ethical" choice with three villains around it — that is a quiz with extra steps.

Territory: credit taken by someone senior, a colleague's mistake you can see and they cannot,
loyalty to a team versus an offer that changes your life, being asked to ship something you
know is not ready, a friend underperforming on your project, choosing between the work you
are good at and the work you want, staying somewhere safe versus leaving with nothing lined up.

## Tone — this matters as much as the content

The game's world is bleak and quiet — a survival aesthetic, charcoal and candlelight, someone
alone in a room deciding things. **Write in that register.** Plain, cold, spare sentences.
Second person. No exclamation marks, no jokes, no encouragement, no emoji.

Say `You have`, not `Imagine you have`. Say `Choose`, not `Which of the following best...`.

## Hard limits — a violation makes the question unusable

- **Prompt: 30 words maximum.** It has to be readable in under nine seconds at the hardest tier.
- **Each option: 12 words maximum.** They sit in four rows on one screen.
- **All four options must be the same shape** — same length, same grammatical form. An option
  that is visibly longer or hedged reads as the answer, and the whole question collapses.
- **Never** use "all of the above", "none of the above", or "both A and B".
- **Never** repeat an answer position pattern — distribute the correct answers across A/B/C/D
  roughly evenly across the 13 general questions.

## Safety floor — non-negotiable, this is run on real people in front of an audience

We are deliberately raising someone's heart rate. That obliges us to keep the content away
from anything that could hurt a real participant rather than merely pressure them.

**Excluded entirely:** self-harm, suicide, sexual content, graphic injury or death, harm to
children, sexual or caste or religious or communal content, medical diagnoses, and anything
targeting a real named person, company, or group.

Pressure comes from **time, stakes, and the impossibility of a clean choice** — never from
distress imagery. If a question would be uncomfortable to run on a stranger at a demo table,
cut it.

## Acts — difficulty rises, time shrinks

Distribute the 20 questions across four acts. Act 1 is a warm-up and is not scored.

| act | questions | seconds each | notes |
|-----|-----------|--------------|-------|
| 1   | 2 general                | none (untimed) | practice. Genuinely easy. |
| 2   | 4 general + 2 career     | 22 | |
| 3   | 4 general + 2 career     | 15 | |
| 4   | 3 general + 3 career     | 9  | hardest, and the career ones bite hardest here |

## Output format — return ONE fenced JSON block, nothing else

A single JSON array of 20 objects, in play order. No prose before or after it.

```json
[
  {
    "id": "a1q1",
    "act": 1,
    "kind": "general",
    "seconds": null,
    "prompt": "A bag holds three red stones and one white. You draw two. What is the chance both are red?",
    "options": [
      { "key": "A", "text": "One half" },
      { "key": "B", "text": "One quarter" },
      { "key": "C", "text": "Nine sixteenths" },
      { "key": "D", "text": "Three quarters" }
    ],
    "answer": "A",
    "rationale": "3/4 x 2/3 = 1/2. C is the trap for drawing with replacement."
  },
  {
    "id": "a4q6",
    "act": 4,
    "kind": "career",
    "seconds": 9,
    "prompt": "Your manager presents your work as theirs, to the people who decide your promotion.",
    "options": [
      { "key": "A", "text": "Correct them in the room, now" },
      { "key": "B", "text": "Raise it with them privately after" },
      { "key": "C", "text": "Say nothing and build the record quietly" },
      { "key": "D", "text": "Take it to their manager instead" }
    ],
    "answer": null,
    "rationale": "No correct answer. Each trades a different thing: standing, relationship, time, or safety."
  }
]
```

**Field rules**
- `id` — `a<act>q<n>`, unique.
- `seconds` — must match the act table above exactly. `null` only in act 1.
- `answer` — the key letter for `general`; **`null` for every `career` question**.
- `rationale` — one sentence. For `general`, say why the right answer is right *and name the
  trap*. For `career`, say what each choice costs. This is never shown to the player; it is
  how we check your work.

## Before you return the file, verify each of these yourself

1. Exactly 20 objects. 13 `general`, 7 `career`.
2. Every `career` question has `"answer": null`. Every `general` question has a letter.
3. No prompt over 30 words. No option over 12 words.
4. Within every question, the four options are the same length and grammatical shape.
5. Correct answers are spread across A/B/C/D — not clustered.
6. Nothing in the safety exclusion list appears anywhere.
7. The act and `seconds` distribution matches the table.
8. It is valid JSON and parses.
