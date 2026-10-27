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

## TO-BE: the approved conversation (build spec, revised 2026-10-27)

Agreed by Cary with Claude (Fable, Opus, Sonnet) and Antigravity, then revised after the
first live test on 2026-10-27. This is what is built in `automations.yaml`.

### Rules
1. **Never send an alert without a yes.** Not from a wake word alone, not from silence,
   not when speech-to-text is down.
2. **Every spoken line is literally true.** After an alert the hub knows one thing:
   whether its message was sent to the caregiver's app.
3. **Talk like a person.** Natural questions, natural answers, one retry if unsure. No
   menus and no keyword-spotting of free speech.
4. **"Stop" means be quiet, anywhere, any time** (whole answer only, with or without
   "VivaJot" in front). It never cancels an alert that was already sent.
5. **Nothing the resident says is ever dropped or forwarded in the background.** After a
   wake there are exactly three outcomes: the all-clear, stop, or the question.
6. **One emergency is one incident row**, enforced in the cloud.

### The okay class (one definition, used everywhere)
A *statement that they are okay*: `[no] (I'm | I am) [doing] (okay | ok | fine | alright |
all right | good | all good | safe) [now]`, plus `all good`, `false alarm`, `never mind`,
`everything is/‘s okay|fine`, `tell my family I'm okay`. A bare "okay", "fine" or "no" is
**not** in the class (ambiguous) and neither is "not hurt" (an answer, not a stand-down).
It is the same whether said as a command ("VivaJot, I'm okay"), as the answer to "Do you
need help?" (= no), or as the answer to "Are you hurt?" (= the all-clear).

### Spoken lines (the complete set; nothing else is spoken)
| Moment | Line |
|---|---|
| Wake, nothing said yet | I'm here. Do you need help? |
| Wake, they already asked for help ("VivaJot, I fell") | I heard you need help. Should I alert your caregiver? |
| Didn't understand | Sorry, I didn't catch that. Do you need help? Just say yes or no. |
| Spoke again, still not understood | I didn't send an alert. If you need help, say VivaJot, help. |
| Could not listen at all (both asks failed, e.g. speech-to-text down) | I can't hear you right now. If you need help, please use your phone. |
| No | Okay, I won't send anything. |
| Alert sent | I've sent your caregiver an alert. Stay where you are. |
| Alert failed | I couldn't send the alert. Please use your phone. |
| Follow-up (once, after a new alert) | Are you hurt? |
| Follow-up answer sent | Okay, I've sent that to your caregiver. |
| Any send failed | I couldn't send that. Please use your phone. |
| "I'm okay" while an alert is active | Okay, I've told your caregiver you're okay. |
| "I'm okay" with no active alert, or "stop" | Okay. |

### A. After any wake (alert active or not)
Wake, chime, then the speaker listens (firmware; never talk over the resident).

| They say | Speaker |
|---|---|
| Nothing | I'm here. Do you need help? |
| A help phrase (help, help me, I need help, I fell, I can't get up, …) | I heard you need help. Should I alert your caregiver? |
| Okay class | Okay. / Okay, I've told your caregiver you're okay. (see D) |
| Stop | Okay. |
| Anything else ("what time is it", "I can't breathe", a mumble) | I'm here. Do you need help? |

**Why the question is asked even during an active alert:** the hub cannot know whether a
caregiver already marked the alert Safe in the app. If it forwarded speech in the
background, "I can't breathe" after a resolved alert could be silently dropped. So it
always asks; a yes during a real alert simply adds to the open incident (one extra
question, harmless), and a yes during a resolved one starts a fresh alert.

The answer to the question:

| Answer | Result |
|---|---|
| Yes class: yes, yeah, yep, please, help, help me, I fell, I can't get up, or **anything that starts with yes/yeah/yep** ("yes, my hip hurts") | Alert (section B) |
| No class: no, nope, no thank you, no I don't, I don't need help, or the **okay class** ("I'm okay", "no I'm fine") | "Okay, I won't send anything." Nothing is suppressed afterwards. |
| Stop | "Okay." Asks nothing more. |
| Unclear or silence | Ask once more ("… Just say yes or no."). Still unclear: if they spoke, "I didn't send an alert…"; if the speaker could not listen at all both times, "I can't hear you right now…"; silence stays silent. Nothing is sent. |

A yes wins when both appear ("no, I need help").

### B. Alert
Publish intent `help` with the resident's words, then speak on the **result**:
- Sent: "I've sent your caregiver an alert. Stay where you are." Start the 24-hour
  incident timer. If no incident was active before this yes, start the follow-up (C).
- Failed: "I couldn't send the alert. Please use your phone." Timer untouched.

### C. Follow-up (about 10 s after a NEW alert; skipped after stop or if the speaker is busy)
"Are you hurt?"
| Answer | Result |
|---|---|
| Okay class ("I'm okay", "no I'm fine") | The all-clear (D): "Okay, I've told your caregiver you're okay." |
| Stop | "Okay." |
| Anything else ("no", "yes my hip", …) | Added to the same incident with their words, caregiver pushed: "Okay, I've sent that to your caregiver." |
| Nothing | Published as "No answer to 'Are you hurt?'", nothing spoken. |
No reminders afterwards.

### D. The all-clear ("I'm okay", okay class, anywhere)
Publish intent `okay`. The cloud marks any open help row Safe with a "cleared by the
resident" note and sends the family an all-clear push when `notify` is set or a row was
actually cleared.
- Incident timer active → `notify: true`, cancel the timer, say "Okay, I've told your
  caregiver you're okay." (always true: that push is always sent while the timer runs).
- No active timer → `notify: false`, say "Okay." (the cloud still clears a stale open
  row if it finds one, and pushes only then).
- Publish failed → "I couldn't send that. Please use your phone."

### Incident state
`timer.vivajot_help_active`, 24 h, `restore: true` (survives the 3 AM restart). Started by
a sent alert, cancelled by the all-clear. Its **only** job is to make the "I'm okay" line
true and to skip a repeat "Are you hurt?" on a re-alert. It never silences anything: a
silent wake always gets the question, and speech is never routed by it. The old
30-minute `input_boolean` is gone.

### Hub changes (built)
- `vivajot_voice_conversation`: catch-all `{utterance}` trigger; routes to all-clear,
  stop, help-confirm, or the question. Nothing reaches Home Assistant's default agent.
- `vivajot_voice_wake_event`: silent wake → question, regardless of incident state.
- `vivajot_voice_help_confirm`: answer lists in sentence-template syntax (`yes {extra}`,
  `[no] (i'm|i am) …`), stop handling in both asks, publish-result branching, the two
  truthful "nothing sent" lines, follow-up only when no incident was active.
- `vivajot_voice_fall_coaching`: okay class → all-clear; stop; else forward with words.
- `viva_renamer.voice_alert` returns `{"published": true|false}`; failures are spoken.
- `timer.vivajot_help_active` replaces the flag; the 30-minute timeout automation is gone.

### Cloud (deployed listener, unchanged by this revision)
- `help`: open help row (99/999, untriaged, last 2 h) → append words + direct push;
  otherwise insert a new 99 row (scanner pushes it).
- `okay`: mark open help rows Safe (24 h) + all-clear push if `notify` or a row cleared.
- `coaching_update`: legacy, kept for hubs that haven't updated.

### Known limits (documented, not fixed)
- The incident timer is per hub. On a shared multi-apartment hub one apartment's alert
  changes only the "I'm okay" wording for the others, nothing else. Single-speaker today.
- Dashboard and push lag the speaker by ~15 s (BigQuery update time). Not a bug.
- Speakers on firmware older than 26.10.22.4 don't report wakes, so a silent wake gets no
  question there; spoken requests still work.

### Timeline tests (run on the real speaker; each is a sequence, not a phrase)
1. **False wake, someone present:** "VivaJot, what time is it" → "I'm here. Do you need
   help?" → "no" → "Okay, I won't send anything." Nothing on the phone or dashboard.
2. **False wake, empty room:** "VivaJot" → silence → question → silence → second ask →
   silence → nothing more, nothing sent.
3. **Real request:** "Help Help" → (silence) → question → "yes" → "I've sent your
   caregiver an alert…" → push + hero within ~15 s → "Are you hurt?" → "no" → "Okay,
   I've sent that to your caregiver" → same card updated, not a second one.
4. **Okay as the follow-up answer:** repeat 3 but answer "Are you hurt?" with "I'm okay"
   → "Okay, I've told your caregiver you're okay." → all-clear push, hero releases.
5. **Okay later:** alert, wait > 30 min, "VivaJot, I'm okay" → the *told your caregiver*
   line (not a bare "Okay."), push arrives, hero releases.
6. **Stale alert:** alert, caregiver taps Mark Safe, then "VivaJot, I can't breathe" →
   question → "yes" → a fresh alert. Then "VivaJot, thank you" → question → "no" →
   nothing sent.
7. **Stop mid-question:** during any question, "VivaJot, stop" → "Okay." and no second
   ask.
8. **Overnight:** alert at 11 PM, no okay, 3 AM restart, "VivaJot, I'm okay" at 7 AM →
   the *told your caregiver* line.

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
3. **Lean on the reliable word in resident training materials:** "Say *VivaJot*, then tell
   it what you need" — "VivaJot, help" wakes on "VivaJot" and the conversation handler
   turns "help" into the confirmation question, so the emergency word is a backup, not
   the only road. (No separate "VivaJot Help" wake word — decided against.)
4. **Retrain `help_help` properly** (the real fix for cause 1/3): more samples, varied
   inter-word gaps and speeds, aged-voice augmentation; see
   [wakewords/README.md](wakewords/README.md) and the Colab notebook. This is the only
   fix for phrases that score ≥ cutoff on garbage ("need to stop" → 1.00 on `vivajot`).
