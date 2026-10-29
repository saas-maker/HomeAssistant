# VivaJot Help Workflow — A-to-Z Map and Gap Analysis

Companion to [VOICE_SYSTEM_ARCHITECTURE.md](VOICE_SYSTEM_ARCHITECTURE.md). This maps every
step and branch of the help workflow **as implemented on 2026-10-23**, audits each branch
against the design standard — *an untrained 85-year-old must always end up safe* — then
lists every gap found and the redesigned flow. The AS-IS and gap sections are kept as
history; the **v4 section at the end is what is built** (2026-10-28), and it passed its first
live tests on a real speaker the same evening (results at the end of the v4 section).

Design standard, restated as testable rules:
- R1. If the resident needs help, help is summoned — even if they can only gasp a wake word.
- R2. If the resident does not need help, no alert goes out — even if they answer imprecisely.
- R3. The resident is never lied to (the speaker never claims an action it didn't do).
- R4. The resident is never confused or frightened by machine language.
- R5. Anything ambiguous resolves toward R1 (fail-safe), never silently toward R2.

---

## AS-IS: the complete flow

### Step 0 — Wake (on the speaker, no cloud involved)
Resident (or the TV) says a wake word. Detection is on-device:
`help_help` (cutoff 0.8, default-enabled), `vivajot` (0.7, enabled on boot).
On wake: chime, LED, any announcement stops, the speaker streams what follows to the hub,
and fires `esphome.vivajot_wake_word` (carrying the wake word + device name).

If the wake word contains "help", the **safety net** (`vivajot_voice_help_wake_event`)
starts a 12-second clock in parallel with everything below.

### Step 1 — What did they say after the wake? (STT on the hub)
| Heard | Path | What the resident experiences |
|---|---|---|
| A help phrase (~27: "help", "I fell", "call 911"…) | `vivajot_voice_help` → cooldown starts → confirmation (Step 2) | Silence for ~1 s, then the confirm question |
| An okay phrase ("I'm okay", "all good"…) | `vivajot_voice_okay` → cooldown starts → publishes intent `okay` | "Great. I have let your family know that you are okay." |
| A stop phrase ("stop", "shut up"…) | `vivajot_voice_stop` → 10-min coaching mute | "Okay." |
| Anything else | Falls through to HA's **default agent** (built-in intents) | Usually "Sorry, I couldn't understand that" — or a raw intent error (the HassLawnMowerDock incident) |
| Nothing (silence) | STT returns empty = benign no-text; pipeline ends | Nothing |
| STT down | Pipeline error | Nothing |

### Step 1b — The 12-second safety net (help wake words only)
At T+12 s, if the cooldown is still idle (no path above claimed the incident), it:
starts the cooldown → **alerts unconditionally** (Risk 99) → announces "Help is on the
way…" → starts coaching. No confirmation. This is the STT-down fail-safe — and the source
of false-wake auto-alerts, because a false wake is followed by silence.

### Step 2 — Confirmation (`vivajot_voice_help_confirm`)
Speaker asks: **"Do you need help? Please say yes or no."**
| Answer | Result |
|---|---|
| Listed "no"/cancel phrase, or cancel-class regex (negation/okay-words AND no danger words) | "Okay, no alert sent. Say Help Help any time you need me." — END (cooldown keeps running ~90 s) |
| Listed "yes"/danger phrase | ALERT path ↓ |
| Anything else, gibberish, or **silence** | ALERT path ↓ (fail-safe) |

ALERT path: `viva_renamer.voice_alert` (intent `help`, quoting what was heard) →
announce "Help is on the way. I have notified your family and caregiver right now.
Please stay still and do not try to stand up." → start coaching.

### Step 3 — Coaching (`vivajot_voice_fall_coaching`, 12 s later, yields to mute/busy)
**"Are you hurt or in pain? Please say yes or no."**
| Answer | Follow-up alert sent | Spoken reply |
|---|---|---|
| Hurt-yes class | "Resident reported they ARE HURT…" | "I have updated your family that you are in pain so they can hurry…" |
| Hurt-no class | "…awake and not in pain, waiting safely" | "…let your family know you are not in pain…" |
| Other speech | "Resident replied <verbatim>" | "I have shared your message with your family…" |
| Silence | "No verbal response… check urgently" | (none) |
Then after 30 s (if not muted/busy): "Reminder: your caregiver has been notified…"

### Step 4 — Cloud (per alert)
`viva_renamer` publishes to Pub/Sub → Cloud Run listener writes the `AI_Risk_Scores` row
(help → Risk 99 "Resident Requested Help!", score 100, idempotent) → push notification to
caregivers → dashboard hero pins the open help row (`is_open_help`) until a caregiver
marks Safe/False-Alarm **or** the resident says "I'm okay" (which marks the same field,
with a "[Cleared by the resident…]" note).

### Step 5 — Resolution
- Caregiver taps Mark Safe / False Alarm → hero releases, row retitled "…was marked safe."
- Resident says "VivaJot… I'm okay" → same release + note (speaker path only; see G9).

---

## Gap inventory (all holes, ranked)

| # | Gap | Breaks | Severity |
|---|---|---|---|
| **G1** | False wake + silence → unconditional alert at T+12 s (Step 1b). TV/dog can summon caregivers. | R2 | **Critical** (active complaint) |
| **G2** | Answering **"okay"** to "Do you need help?" CANCELS — the cancel regex treats ok/okay/fine/alright as negations, but in answer to an offer of help, "okay" usually means *accept*. An elderly resident who fell and answers "okay…" gets "no alert sent." | R1, R5 | **Critical** |
| **G3** | **The speaker lies on "I'm okay."** It says "I have let your family know that you are okay," but the listener's okay branch only marks the dashboard row Safe — **no all-clear push is ever sent** to family. | R3 | **High** |
| **G4** | **90-second deaf window after a cancel.** A cancel leaves the cooldown running; if the resident then genuinely falls and can only gasp "Help Help" (wake, no speech), the safety net sees cooldown ≠ idle and stays silent. No alert. | R1 | **High** |
| **G5** | Saying **"stop"** to "Do you need help?" is unmatched → alerts. Everywhere else "stop" means "leave me alone." Defensible as fail-safe, but inconsistent; a resident silencing a false wake with "stop" still pages the family. | R2, R4 | Medium (tied to G1) |
| **G6** | **Pub/Sub failure is swallowed**: if the publish fails, the resident is still told "Help is on the way." No retry, no local fallback, no honest "I could not reach your family — please call them." | R1, R3 | Medium (rare, worst-case severe) |
| **G7** | Unmatched speech falls into HA's **built-in intent library**: robotic "Sorry, I couldn't understand that," or raw errors like "Unknown intent HassLawnMowerDock." Frightening/confusing machine language. | R4 | Medium |
| **G8** | **Cooldown doubles as the "incident active" flag but only lasts 90 s** — shorter than a full confirm+coaching cycle. Near the end of an incident a new help wake can double-alert; and "already in help mode" checks are only as good as this timer. Incident state needs a first-class signal, not a side effect. | R2 | Medium |
| **G9** | **Alexa "I'm okay" doesn't clear the alert** (writes a separate risk-0 row). Fail-safe but inconsistent with the speaker. | R3 (consistency) | Low-Medium |
| **G10** | Wake-path events carry `device_id: ""` → coaching may speak from the **wrong room's speaker** in multi-speaker homes. | R4 | Low (single-speaker today) |
| **G11** | Repeat help phrases during an active incident re-run confirm → duplicate alert rows (conversation path never checks the cooldown; only pushes are deduped downstream). | — | Low |

---

## TO-BE v4: the built conversation (2026-10-28)

Agreed by Cary after the wake-word measurements below. Built in `automations.yaml`,
`configuration.yaml` and firmware 26.10.28.1. Supersedes the 2026-10-27 spec.

### Rules
1. **One wake word: "VivaJot".** The speaker listens for nothing else. "Help Help" is retired
   (it missed 16% of clean synthetic clips) and no new emergency word is trained until a
   model measures better than today's on the bench (see measurements).
2. **The question comes straight after the chime.** The speaker does not listen for a
   command first. Every wake, alert active or not, leads to one yes/no question.
3. **Nothing is sent without a yes.** Not from a wake alone, not from silence, not when
   speech-to-text is down.
4. **False wakes are a first-class failure, equal to misses.** A speaker that keeps waking
   and asking gets unplugged, and an unplugged speaker misses everything.
5. **Residents never have to guess the words.** The question says "yes or no"; dozens of
   ways to say each are accepted (lists below). A bare "okay" is ambiguous and gets the
   question once more.
6. **Every spoken line is literally true** and only said after the send succeeded.
7. **"Stop" means be quiet**, anywhere, any time, with or without "VivaJot" in front. It
   never cancels an alert.

### The flow
**No alert active.** "Do you need help? Yes or no."

| Resident | Speaker | Sent |
|---|---|---|
| yes class | "I've notified your caregiver. Stay where you are." | help alert |
| no class | "Okay." | nothing |
| stop | silence | nothing |
| words that are not an answer | "Sorry, I didn't catch that. Yes or no?" then the same three outcomes; unclear or silent again → silence | nothing |
| silence | silence (not asked twice: an empty room after a false wake) | nothing |

There is no "I can't hear you" line. A silent or unrecognized answer is the normal false-wake case
(empty room), and a speaker that talks to an empty room gets unplugged.

**Alert already active** (2 h incident timer running). "Your caregiver has already been
notified. Do you still need help? Yes or no."

| Resident | Speaker | Sent |
|---|---|---|
| no class ("no", "I'm okay", …) | "Okay, I've told your caregiver you're okay." | all-clear: open help row marked Safe, caregiver pushed |
| yes class | "Tell me what's wrong." → whatever they say → "Okay, I've sent that to your caregiver." | their words added to the same incident, caregiver pushed |
| stop | silence | nothing |
| unclear | asked once more, as above | nothing |

A send that fails is always spoken: "I couldn't send the alert / that. Please use your phone."

### Answer classes (the complete lists live in `vivajot_voice_help_confirm`)
- **Yes** (≈70 forms): yes, yeah, yep, yup, uh huh, mm hmm, sure, please, I think so, help,
  help me, I need help, somebody help, send/get/call help, call my family/daughter/son/the
  nurse, I fell, I can't get up, I can't move, I'm hurt, I'm bleeding, I can't breathe, I'm
  not okay, I'm sick/dizzy/in pain, emergency, anything starting with yes/yeah/yep/help,
  and "no I need help" / "no I fell" (a yes wins when both appear).
- **No** (≈35 forms): no, nope, nah, no thanks, not now, I don't need help/anything, just
  testing, cancel, that's okay, it's fine, and the okay class: [no] I'm/I am [doing|feeling]
  okay/ok/fine/alright/good/safe/better [now], all good, false alarm, never mind, forget it,
  I'm good, everything is/'s okay/fine.
- **Stop**: stop, VivaJot stop, shut up, be quiet, quiet, stop talking, that's enough,
  enough, leave me alone, no more.
- **Not an answer** (asked again): okay, fine, hello, what, "I can't stop bleeding" (not a
  stop: stop is only a whole answer), anything else.

### Incident state
`timer.vivajot_help_active`, 2 h (the cloud incident window), `restore: true`. Started by every successful help send,
cancelled by the all-clear. It only selects which question is asked and keeps the "told your
caregiver" line truthful. After 2 h the speaker asks the plain "Do you need help?" again, and a
new yes starts a new incident (24 h made it say "already notified" the next morning). The 10-minute mute timer and the automatic "Are you hurt?"
follow-up are gone: a resident who wants to add something says "VivaJot" again.

### Hub automations (3)
- `vivajot_voice_wake_event`: logs the wake to the Logbook ("VivaJot wake: vivajot on
  <speaker>"), then fires the question. With firmware 26.10.28.1+ (`ask: true` in the event)
  it asks at once; with older firmware it waits for the speaker's own listen to finish and
  asks only if the conversation handler did not take the speech.
- `vivajot_voice_help_confirm`: the question, the retry, the two modes, the sends.
- `vivajot_voice_conversation`: safety net for older firmware or any speech that still
  reaches the pipeline: stop, the okay class (all-clear), or the question. Nothing falls
  through to Home Assistant's built-in intents.

### Firmware 26.10.28.1
- Models: `vivajot` only (`probability_cutoff` 0.80, `sliding_window_size` 5). `help_help`,
  `vivajot_help` and `somebody_help` are not in the firmware.
- On wake: chime, fire `esphome.vivajot_wake_word` with `ask: "true"`, and do **not** start
  the local command listen. A wake during an announcement stops the announcement.
- Everything else (OTA from this repo, watchdog, stock `stop` word during announcements)
  unchanged.

### Cloud (deployed listener, unchanged)
`help` appends to an open incident (2 h) or inserts a new 99 row; `okay` marks open rows Safe
and pushes the all-clear when `notify` is set.

### Speech-to-text and quiet answers (2026-10-28)
Live speaker log: the speaker heard the resident (voice detected) but Google returned "no text
recognized" for one-word answers. Probe with synthetic clips: a "no" or "help" 40 dB below normal
volume was empty from Google raw and recognized after `stt.py` raises the level (peak to 9000,
gain capped at 40x, silence left alone) and adds 0.3 s of silence each side. The hub log now
prints `VivaJot STT: audio 1.7s, peak 314 -> 9000` for every answer, so a real resident's level
can be read off the Logs page. Not yet verified with a real voice.

### Timing bug found live (2026-10-28)
The wake chime (0.95 s) plays as an announcement. When the hub's question arrived while the chime
was still playing, the speaker's player never switched into "announcing", so the speaker fell back
to a 2-second timer and opened the mic mid-question. It then transcribed its own voice ("I've been
notified") as the answer and the next line started at once. The hub now waits 1.5 s before the
first question, so the player is idle and the speaker waits for the question to really finish.
A firmware-side fix (send the wake event after the chime ends) would remove the wait; not done.

### The caregiver log (2026-10-28)
Every help request stays in the app's Event Log under its own name, open or resolved, tagged
"Resident said they're okay", "Marked safe" or "Marked false alarm". Before this, `api.ashx`
returned only the newest resolved row and `default.js` retitled resolved rows "Marked Safe"
and folded them into the hidden routine checks, so a request vanished once the resident said
they were okay. The resident's later words ("my foot hurts") are appended to the same row.

### Known limits
- "help" shouted without "VivaJot" does nothing. Teaching, a card by the speaker, and the
  phone/pendant are the mitigations.
- The question starts right after the chime, so "VivaJot I fell" said in one breath may lose
  its first word; "I fell" and "fell" variants are in the yes class.
- The incident timer is per hub (single-speaker today).
- Dashboard and push lag the speaker by ~15 s (BigQuery update time).
- The question starts ~1.5 s after the wake word (the hub waits out the chime, see the timing
  bug above). Firing the wake event after the chime, in firmware, would remove the wait.

### Timeline tests (real speaker)
1. **False wake, someone present:** "VivaJot" → "Do you need help? Yes or no." → "no" →
   "Okay." Nothing on the phone or dashboard.
2. **False wake, empty room:** "VivaJot" → question → silence → nothing more, nothing sent
   (silence is not asked twice).
3. **Real request:** "VivaJot" → question → "yes" → "I've notified your caregiver. Stay where
   you are." → push + hero within ~15 s. Logbook shows "VivaJot wake: vivajot".
4. **Still needs help:** "VivaJot" → "Your caregiver has already been notified. Do you still
   need help?" → "yes" → "Tell me what's wrong." → "my hip" → "Okay, I've sent that to your
   caregiver." → same card updated with "my hip", not a second card.
5. **All-clear:** "VivaJot" → "…Do you still need help?" → "no" (or "I'm fine") → "Okay, I've
   told your caregiver you're okay." → all-clear push, hero releases.
6. **Stale alert:** caregiver taps Mark Safe on the app, then "VivaJot" → the speaker still
   asks "…Do you still need help?" (within the 2 h incident window) → "yes" → words → a fresh alert.
7. **Stop mid-question:** during any question, "VivaJot, stop" or "stop" → silence, no retry.
8. **Soft voice:** whisper "yeah" / "nope" / "uh huh" from across the room; each must land.
9. **Overnight:** alert at 2:30 AM, 3 AM restart, "VivaJot" at 3:15 AM → the "still need help"
   question (timer restored). "VivaJot" at 7 AM → the plain "Do you need help?" (window over).

### Live test results (2026-10-28 evening, Cary's speaker, firmware 26.10.28.1)
| Test | Result |
|---|---|
| 3 Real request | Pass: "yes" → alert pushed with the resident's words |
| 4 Still needs help | Pass: "my foot hurts", then "my foot still hurts and I can't play the banjo anymore" appended to the same incident with times |
| 5 All-clear | Pass: incident marked Safe; the Event Log keeps the request tagged "Resident said they're okay" |
| 1, 2, 6, 7, 8, 9 | Not yet run |

Found and fixed during these tests: the speaker opened the mic mid-question (timing bug above),
Google returned nothing for quiet one-word answers (speech-to-text section), the retry and
"I can't hear you" lines rambled at an empty room, the active-alert question now says "already
been notified", and the Event Log hid resolved help requests.

---

## Wake-word measurements (2026-10-28) and the decision

Bench: microWakeWord's own inference over the same audio for every model, using the
speaker's detection rule (mean of the last 5 outputs > cutoff, ~2 s refractory). FAPH = false
accepts per hour. Full numbers in the session results file; summary:

| Audio | Model | FAPH @0.75 | @0.80 | @0.85 | @0.90 | @0.95 |
|---|---|---|---|---|---|---|
| DipCo dinner party, 134 min | vivajot (deployed) | 9.0 | 8.1 | 6.7 | 5.4 | 3.1 |
| | help_help (deployed) | 0.9 | 0.45 | 0.0 | 0.0 | 0.0 |
| | somebody_help (new, 20k steps, extra music negatives) | 7.2 | 5.4 | 4.0 | 2.2 | 1.3 |
| CHiME-6 homes, 119 min | vivajot | 0.5 | 0.0 | 0.0 | 0.0 | 0.0 |
| | help_help | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| | somebody_help | 3.0 | 2.5 | 2.5 | 2.0 | 2.0 |
| FMA music, 10 min held out | all three | 5.95 | 0.0 (help_help 5.95) | 0.0 | 0.0 | 0.0 |

Recall on clean synthetic clips (18 Piper voices): help_help 84% at 0.75; somebody_help 97%.
Cross-phrase 0%.

What it says:
- The deployed **VivaJot model is the main false-wake source**, not Help Help.
- **Help Help is quiet but deaf** (misses 16% even of clean voices) — the missed real calls.
- The new Somebody Help model hears well but false-wakes 7×/h at 0.75; the "stronger"
  recipe (negative weight 40, 20k steps, extra music negatives) did not beat the original
  recipe on false accepts. The root cause is the training data: 18 synthetic voices, thin
  negatives, no sound-alike phrases, and the notebook's AudioSet download is silently empty.
- Reference: Nabu Casa's `okay_nabu` is 0.000 FAPH on DipCo at 0.85.

Decision: **VivaJot only, cutoff 0.80**, question on wake, big yes/no lists. The Logbook now
records every wake with its word, so the next cutoff change is made from real counts, not
from guesses. Next (one model for the whole fleet, done once): retrain VivaJot with the full
recipe (many more voices, sound-alike negatives, working noise sets, false-accept check
during training on paid Colab), measure it on this bench against the deployed model, ship
only if better. Never per-house training.
