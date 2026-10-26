# VivaJot Help Workflow — A-to-Z Map and Gap Analysis

Companion to [VOICE_SYSTEM_ARCHITECTURE.md](VOICE_SYSTEM_ARCHITECTURE.md). This maps every
step and branch of the help workflow **as implemented on 2026-10-23**, audits each branch
against the design standard — *an untrained 85-year-old must always end up safe* — then
lists every gap found and the proposed redesigned (TO-BE) flow. Analysis only; nothing
here is implemented yet.

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

## TO-BE: the approved conversation (build spec, 2026-10-25)

Agreed by Cary, Claude (Fable, Opus, Sonnet) and Antigravity. This is the build spec and
replaces every earlier TO-BE draft.

### Rules
1. **Never send an alert without a yes.** Not from a wake word alone, not from silence,
   not when speech-to-text is down. If the resident could say the wake word, they can
   answer the question.
2. **Every spoken line is literally true.** After an alert the hub knows one thing:
   whether its message was sent to the caregiver's app. Never "help is on the way",
   "they're coming", or "hurry".
3. **Talk like a person.** Ask natural questions, accept natural answers, ask once more
   if unsure. No menus ("say X or say Y") and no keyword-spotting of free speech
   ("can't", "fell" and "hurt" all have innocent daily uses).
4. **"Stop" means be quiet, anywhere, any time.** The speaker stops talking, says
   "Okay." and asks nothing more. It never cancels an alert that was already sent.
   Stop counts only when it is the whole answer ("stop", "shut up", "be quiet", with or
   without "VivaJot" in front); a sentence that merely contains the word ("I can't stop
   bleeding", "stop and listen") is not a stop.
5. **One emergency is one incident row**, enforced in the cloud.

### Spoken lines (the complete set; nothing else is spoken)
| Moment | Line |
|---|---|
| Wake, nothing said yet | I'm here. Do you need help? |
| Wake, they already asked for help ("VivaJot, I fell") | I heard you need help. Should I alert your caregiver? |
| Didn't understand | Sorry, I didn't catch that. Do you need help? |
| No | Okay, I won't send anything. |
| Alert sent | I've sent your caregiver an alert. Stay where you are. |
| Alert failed | I couldn't send the alert. Please use your phone. |
| Follow-up (once, after the alert) | Are you hurt? |
| Anything said during an active alert | Okay, I've sent that to your caregiver. |
| "I'm okay" during an active alert | Okay, I've told your caregiver you're okay. |
| "I'm okay" with no active alert, or "stop" | Okay. |

"Sent an alert" is used instead of "alerted" because the hub can't know anyone read it.
"I've told your caregiver you're okay" is true because that push is always sent while
an alert is active (see Cloud changes).

### A. No active alert
Wake, chime, then the speaker listens (the firmware already does this; never talk over
the resident).

| They say after the wake | Speaker |
|---|---|
| Nothing | I'm here. Do you need help? |
| A request for help (help, help me, I need help, I fell, I can't get up) | I heard you need help. Should I alert your caregiver? |
| I'm okay, I'm fine, never mind, false alarm | Okay. (publishes `okay`, which clears any older open alert) |
| Stop | Okay. |
| Anything else ("what time is it", a mumble) | I'm here. Do you need help? |

The answer to the question:

| Answer | Result |
|---|---|
| Yes, yeah, yep, please, yes please, help, help me, I fell, I can't get up | Alert (section B) |
| No, nope, I'm okay, I'm fine, I'm alright, never mind, false alarm | "Okay, I won't send anything." The flow ends and nothing is suppressed afterwards. |
| Stop, shut up, be quiet (with or without "VivaJot") | "Okay." The flow ends; the second ask and the follow-up are skipped. |
| Unclear, or silence | Ask once more: "Sorry, I didn't catch that. Do you need help?" Same answers apply. Unclear or silent again: say nothing more, send nothing. |

A yes wins if both appear ("no, I need help"). A bare "okay" is unclear (it can mean
either), so it gets the second ask.

### B. Alert
The hub publishes intent `help` with the resident's own words, then speaks based on the
result of the publish:
- Sent: "I've sent your caregiver an alert. Stay where you are." The incident flag
  turns on.
- Failed: "I couldn't send the alert. Please use your phone." The flag stays off, so the
  next wake asks again.

### C. Follow-up
About 10 seconds after the alert line, unless they said stop: "Are you hurt?"
- Any answer is published with their words, then "Okay, I've sent that to your caregiver."
- No answer: publish "No answer to 'Are you hurt?'" and say nothing.

Then the speaker is quiet. There are no reminders.

### D. Active alert (incident flag on)
A wake never re-asks the question. Chime, then listen:

| They say | Result |
|---|---|
| I'm okay, I'm fine, false alarm | Publish `okay` with notify, then "Okay, I've told your caregiver you're okay." The flag turns off. |
| Stop | Okay. |
| Anything else | Published with their words, then "Okay, I've sent that to your caregiver." |
| Nothing | Nothing. |

The flag turns off on "I'm okay" or after 30 minutes. If it expires before a caregiver
handles the alert, a later wake asks "Do you need help?" again, and a yes is folded into
the same open incident by the cloud (rule 5), so nothing is duplicated.

### Hub changes (automations.yaml, configuration.yaml, viva_renamer)
- One conversation automation with a catch-all sentence trigger classifies all speech
  after a wake (help request, okay, stop, other). Nothing falls through to Home
  Assistant's default agent, which removes replies like "Unknown intent
  HassLawnMowerDock".
- The wake-event automation handles the "said nothing" case: once the satellite is idle
  again with no conversation, it asks "I'm here. Do you need help?" It never alerts on
  its own.
- The question and the second ask use `assist_satellite.ask_question` with the answer
  lists above, with `continue_on_error`: an STT failure counts as unclear, never as yes.
- `viva_renamer.voice_alert` must report whether the Pub/Sub publish succeeded (today it
  logs and swallows failures). The alert line branches on that result.
- Incident flag: `input_boolean.vivajot_help_active`, on after a successfully sent alert,
  off on "I'm okay" or after 30 minutes.
- "Stop" starts the existing `timer.vivajot_coaching_mute`, which cancels a pending
  follow-up question.
- `okay` publishes carry `notify: true` while the incident flag is on.
- Retire: the 12-second wake-word alert, the 90-second cooldown, the cancel regex and
  danger-word overrides, the 30-second reminder, and every old spoken line.

### Cloud changes (Cloud Run listener; Cary deploys, Claude and Antigravity never deploy)
- `help` intent: if this installation has an open help row (risk 99 or 999, not marked
  Safe or False Alarm, from the last 2 hours), UPDATE that row by appending
  " | <time>: <resident's words>" to its reasoning, and send a push with those words
  directly through SMNotify. Otherwise INSERT the row as today (99,
  "Resident Requested Help!", score 100), which the scanner pushes.
- `okay` intent: mark open help rows Safe with the cleared-by-resident note (as today),
  then send an all-clear push if `notify` is true or the UPDATE cleared at least one row.
  Otherwise push nothing.
- `coaching_update` is no longer sent by the hub. Leave the branch in place for hubs that
  haven't updated yet.

### Dashboard
No change needed: the hero pin is live. Optional: pin any open help row, not only the
newest one.

### Wake-word tuning (only after the flow ships)
Under this flow a false wake can never page anyone, but it does speak one question into
the room, possibly at night. Measure the false-wake rate on the new flow first. Then, if
an occasional question is acceptable, lower `help_help` from 0.8 to about 0.65 and enable
the already trained `vivajot_help` model. In the same firmware rebuild, remove the stock
on-device "stop" word: it cuts the speaker off whenever "stop" is heard during speech,
even inside a sentence, which contradicts rule 4 ("VivaJot, stop" already covers it).
Retraining `help_help` with more and older voices remains the long-term fix (see
[wakewords/README.md](wakewords/README.md)).

---

## "Help Help" is missed; "VivaJot" is reliable — why, and what to do

**Why.** Three compounding causes:
1. **Phonetics.** "Help help" is one short, breathy, plosive-heavy syllable repeated —
   microWakeWord models thrive on longer, phonetically varied words ("okay nabu",
   "hey jarvis", "vee-va-jot"). A repeated word also has weak temporal structure: real
   people vary the gap and cadence between the two "help"s far more than the synthetic
   training samples did.
2. **Cutoff asymmetry.** After the false-wake problems, `help_help` was raised to **0.8**
   while `vivajot` sits at 0.7. The **emergency word now has the system's highest miss
   rate** — backwards for a safety product: missing a real "Help Help" costs far more
   than a false wake.
3. **Training data.** The model was trained on synthetic voices; elderly voices (slower,
   quavery, "heeelp… help") sit at the edge of its distribution.

**What to do (in order of leverage):**
1. **Ship the TO-BE confirmation flow first.** Once every idle wake is answered by
   "I'm here. Do you need help?", a false wake costs one spoken question that ends in a
   "no" or in silence, never a caregiver page. That frees us to tune wake words for
   **recall** (measure the false-wake rate on the new flow first).
2. **Then lower `help_help` to ~0.6–0.65** (manifest JSON change, no retrain). More false
   wakes, far fewer misses — and every false wake is absorbed by the question.
3. **Enable `vivajot_help` ("VivaJot Help") as a third active model.** Already trained and
   shipped in firmware, 4 syllables + distinct phonetics (it detected at 0.85–0.94 in
   field tests). One `on_boot` line.
4. **Lean on the reliable word in resident training materials:** "Say *VivaJot*, then tell
   it what you need" — the conversation path catches all 27 help phrases after a VivaJot
   wake, so the emergency word is a backup, not the only road.
5. **Retrain `help_help` properly** (the real fix for cause 1/3): more samples, varied
   inter-word gaps and speeds, aged-voice augmentation; see
   [wakewords/README.md](wakewords/README.md) and the Colab notebook. This is the only
   fix for phrases that score ≥ cutoff on garbage ("need to stop" → 1.00 on `vivajot`).
