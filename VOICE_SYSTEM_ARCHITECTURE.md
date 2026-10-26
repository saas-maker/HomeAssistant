# VivaJot Voice Safety System — Architecture

How a resident's spoken "Help help" becomes a caregiver alert, end to end: speaker firmware,
Home Assistant hub, Pub/Sub transport, Cloud Run, BigQuery, and the VivaJot dashboard.
Written for engineering review (2026-10-23). Wake-word **training** is documented separately
in [wakewords/README.md](wakewords/README.md) and
[wakewords/HOW_TO_MAKE_NEW_WAKEWORDS.txt](wakewords/HOW_TO_MAKE_NEW_WAKEWORDS.txt).

## System map

```
┌─────────────────────┐   wake word     ┌──────────────────────────────┐
│ HA Voice PE speaker │ ──(on device)─▶ │ Home Assistant hub (Green)   │
│ microWakeWord       │   audio only    │  stt.py (Google Cloud STT)   │
│ 3 custom models     │   after wake    │  conversation automations    │
└─────────────────────┘                 │  custom_components/          │
                                        │    viva_renamer              │
                                        └──────────────┬───────────────┘
                                                       │ Pub/Sub publish
                                                       ▼
                     topic: vivajot-setup-confirmation (also → BigQuery
                     Device_Setup_History via a BigQuery subscription)
                                                       │ push subscription
                                                       │ vivajot-voice-alert-push
                                                       ▼
                                        ┌──────────────────────────────┐
                                        │ Cloud Run                    │
                                        │ vivajot-device-listener      │
                                        │  · writes AI_Risk_Scores     │
                                        │  · marks rows Safe on "okay" │
                                        │  · dispatches push notifs    │
                                        └──────────────┬───────────────┘
                                                       ▼
            BigQuery VivaJot dataset  ◀──  AI risk scorer (scheduled view,
            AI_Risk_Scores et al.          prompts from Lookup_Risk_Prompts)
                                                       │
                                                       ▼
                                        VivaJot web plugin (api.ashx +
                                        js/default.js): dashboard hero,
                                        Event Log, push via SMNotify.ashx
```

---

## 1. The speaker (ESPHome firmware)

Hardware: Home Assistant Voice Preview Edition (ESP32-S3). Firmware is a VivaJot fork of
the Nabu Casa YAML, project `SaaS Factory.VivaJot Voice PE`, currently **v26.10.22.6**.

**Privacy model.** Wake-word detection is 100 % on-device (microWakeWord). No audio leaves
the room until a wake word fires — required for health-facility deployments. Only after a
wake does the speaker stream the following utterance to the hub for STT.

**Wake-word models** (shipped in firmware, trained per the wakewords README):

| Model | Purpose | Probability cutoff |
|---|---|---|
| `help_help` ("Help Help") | Emergency word, default-enabled (listed first) | 0.8 |
| `vivajot` ("VivaJot") | Assistant word, enabled by `on_boot` | 0.7 |
| `vivajot_help` ("VivaJot Help") | Trained but **not used** (decided against: "VivaJot" + spoken "help" covers it) | — |
| `stop` (stock) | Interrupts TTS playback locally | stock |

Only the first-listed model is enabled by default in the ESPHome component; the others are
enabled with `micro_wake_word.enable_model` in `on_boot`. Cutoffs were raised from the 0.6
default after false wakes ("peld peld", "you need to stop" → 0.78–1.00). Cutoffs cannot fix
a model that scores garbage phrases ≥ its cutoff — that requires retraining with more
samples (open backlog item).

**Firmware behaviors added for VivaJot:**
- Every wake fires a Home Assistant event `esphome.vivajot_wake_word` with `wake_word` and
  `device_name` — the hub's STT-independent safety net keys on this (see §2).
- A wake during an announcement stops playback (`media_player.stop`, 200 ms delay) and
  starts listening: a wake word must always be heard, never talked over.
- A 120 s watchdog restarts microWakeWord if the voice assistant pipeline wedges
  (fixes the "speaker goes permanently deaf after an error" failure).

**Distribution.** Install page: `https://vivajot.com/Plugins/VivaJot/flash.html` (ESP Web
Tools one-button USB flash + Improv BLE Wi-Fi provisioning; the page is public, the app's
SMART SPEAKER entry is admin-only). OTA updates come from this repo:
`wakewords/firmware/ota_manifest.json` (and `ota_manifest_beta.json`), served via
raw.githubusercontent (≈5-minute cache). `*.factory.bin` is the USB image (offset 0);
`*.ota.bin` + MD5 is the OTA image. `.gitattributes` marks `*.bin`/`*.tflite` binary —
CRLF conversion once corrupted a factory image; verify byte-equality after committing
firmware. JSON manifests must be written **without a UTF-8 BOM** (BOM breaks the device's
parser). The hub auto-adopts speakers (`_auto_adopt_voice_speakers` in viva_renamer) — the
HA companion app is never required.

---

## 2. The hub (Home Assistant)

### Speech-to-text — `stt.py`
Custom STT entity using Google Cloud Speech v1 with a `speechContexts` phrase boost (boost
20) over the emergency vocabulary; 12 s stream timeout. An **empty transcript returns
SUCCESS with no text** (not ERROR): wake-only events are normal, and the firmware quietly
ignores `stt-no-text-recognized`, whereas an STT error used to leave the speaker in a bad
state.

### Shared timers — `configuration.yaml`
| Timer | Duration | Meaning |
|---|---|---|
| `timer.vivajot_alert_cooldown` | 1 m 30 s | "A help incident is being handled." Suppresses duplicate alerts from both the conversation path and the wake-word safety net. |
| `timer.vivajot_coaching_mute` | 10 m | Started by "stop"/"shut up". Coaching questions and reminders stay silent while running. **Never cancels an alert.** |

### Voice UX doctrine (as currently implemented)
1. A wake word always makes the speaker listen (chime), never lecture.
2. Occasional scheduled announcements are fine, but scheduled speech **yields**: coaching
   never speaks while the satellite is busy or while the mute timer runs.
3. A help request is **confirmed before alerting** ("Do you need help?").
4. Residents are untrained 85-year-olds: matching is class-based, not phrase lists. A
   cancel is negation/okay words with **no** danger words (regex in the confirm automation);
   anything ambiguous falls through to the alert.
5. Fail-safe default: **yes, unclear speech, or silence → alert. Only a clear "no" cancels.**
6. "Stop" (and "shut up", etc.) silences coaching for 10 minutes; it never cancels an alert.
7. Only "I'm okay" (and variants) clears an active incident.

### The automations (`automations.yaml`)

**`vivajot_voice_help`** — conversation trigger on ~27 help phrases ("help", "I fell",
"call 911", …). Starts the cooldown (marks the incident as claimed) and fires the
`vivajot_confirm_help` event. No alert yet.

**`vivajot_voice_help_confirm`** — `assist_satellite.ask_question`: *"Do you need help?
Please say yes or no."* Explicit no / cancel-class answer → "Okay, no alert sent."
Everything else (yes, danger words, gibberish, **silence**) → `viva_renamer.voice_alert`
(intent `help`), spoken reassurance ("Help is on the way…"), then fires coaching.

**`vivajot_voice_fall_coaching`** — 12 s later (if not muted and satellite idle):
*"Are you hurt or in pain?"* Each outcome publishes a follow-up alert (intent `help`,
details prefixed "Help follow-up: …" — hurt / not hurt / verbatim reply / no response),
then a 30 s calming reminder (also yields to mute/busy). `mode: single, max_exceeded:
silent` so re-triggers can't stack sessions.

**`vivajot_voice_help_wake_event`** — the STT-independent safety net. Fires on
`esphome.vivajot_wake_word` when the wake word contains "help". Waits 12 s; if the
cooldown is still idle (meaning the conversation path never claimed the incident — STT
down or no speech followed), it alerts unconditionally. *This is the path behind false
"Help Help" wakes auto-sending help — see Known issues.*

**`vivajot_voice_stop`** — "stop", "shut up", "be quiet", … → starts the 10-minute mute,
replies "Okay."

**`vivajot_voice_okay`** — "I'm okay/fine/safe/all good/false alarm" → starts the cooldown
(resolves in-flight flows), replies, and publishes intent `okay` (which marks the help row
Safe in BigQuery — see §4).

### Multi-apartment hubs
Device names may carry an `@APT` tag; `viva_renamer` resolves the speaker's device name to
a **virtual installation id** (`CARYS-HOUSE:APT1`) so one hub serves several apartments.
All cloud rows and alerts are scoped by that id.

---

## 3. Transport (Google Pub/Sub, project `saas-maker-api-project`)

| Topic | Producers → Consumers |
|---|---|
| `vivajot-setup` | Server commands → per-hub subscriptions (`viva-cmd-<install>`). Carries `{"command":"emergency_update","installation_id":…}` which makes a hub pull config immediately instead of waiting for the nightly cycle. |
| `vivajot-setup-confirmation` | Hubs publish confirmations **and voice alerts** → (a) BigQuery subscription writing `VivaJot.Device_Setup_History`, (b) **push subscription `vivajot-voice-alert-push` → Cloud Run listener**. |
| `home-assistant-events` | Hub telemetry (filtered entity states) → BigQuery subscription writing `VivaJot.Home_Events` (the AI scorer's raw material, and the dashboard's hub-offline heartbeat). |

A voice alert message looks like:
```json
{ "inst_id": "CARYS-HOUSE", "entity_id": "viva_renamer.voice_alert",
  "action_type": "VOICE_ALERT", "status": "TRIGGER",
  "friendly_name": "Alert:help", "action_request": "<details, max 200 chars>" }
```
The intent travels in `friendly_name` after the colon: `help`, `okay`, or `coaching_update`.

---

## 4. Cloud Run — `vivajot-device-listener` (us-central1)

One Python function (`main.py`, entry `vivajot_device_listener`, python314 buildpack,
deployed from the Cloud Run console source editor). Source mirror in this workspace:
`C:\SaaSMaker2026\Google\CloudRun\VivaJot_Listener\` — the **deployed** source is
canonical; keep the mirror in sync after each deploy. It has two jobs:

**A. Pub/Sub push handler** (requests containing `message`): decodes the payload, ignores
everything that isn't `VOICE_ALERT`/`TRIGGER`, then by intent:

| Intent | Action |
|---|---|
| `help` (default) | INSERT into `AI_Risk_Scores`: **risk_id 99, risk_name `Resident Requested Help!`, score 100, alert_triggered TRUE**, reasoning quotes the resident's words. Idempotent via `row_guid = voice-<pubsub messageId>`. Falls through to the push dispatcher so the caregiver push goes out immediately. |
| `okay` | UPDATE: marks untriaged **help rows only** (`risk_id IN (99, 999)`, last 24 h, same installation) `safe_falseAlarm_status = 'safe'` and appends `[Cleared by the resident: they said they are okay via the Smart Speaker.]` to the reasoning. This is deliberately **the same field the caregiver's Mark Safe button sets** — one release mechanism, two triggers. Never touches AI-raised alerts. |
| `coaching_update` | INSERT risk_id 0 "AI Coaching Added", alert_triggered FALSE (record-only). |

**B. BigQuery remote-function scanner** (requests containing `calls`, invoked on the AI
scoring cycle): queries fresh `AI_Risk_Scores` rows (last 3 min), applies push cooldown
tiers (score > 90: hourly; ≤ 90: every 4 h; new/triaged: instant), posts
`"RISK_NAME|reasoning"` to `SMNotify.ashx` (the service worker splits on `|` for push
title/body), and records sends in `Push_Notification_History` for dedup.

---

## 5. BigQuery (dataset `VivaJot`)

| Table / view | Role |
|---|---|
| `AI_Risk_Scores` | The alert ledger. Every row: installation_id, snapshot_time, risk_id, risk_name, risk_score, alert_triggered, clinical_reasoning, `safe_falseAlarm_status` (NULL / 'safe' / 'false alarm'), row_guid. Written by the AI scorer, the Cloud Run listener (voice), and api.ashx (Alexa/server voice endpoint). |
| `Home_Events` | Raw hub telemetry (Pub/Sub BigQuery subscription). Input to the scorer; also the dashboard's offline check (no event in 15 min → "Hub Offline"). |
| `Lookup_Risk_Prompts` | The AI scorer's prompt templates, versioned by rows: `Template_Type` 'Persona' / 'Guidelines', newest `created_at` wins. To change scorer behavior you INSERT a new row, never edit code. |
| `Home_Profile_Habits` | Per-resident habits + nickname; context for the scorer. |
| `v_AI_Risk_Scores_new` (view, scheduled) | **The AI scorer.** Builds a prompt from the latest Persona + Guidelines, habits, and the activity timeline; the model returns RISK_ID / RISK_NAME / RISK_SCORE / REASONING rows. The scorer never sees voice events — voice is a *fact* recorded directly by the listener, not something the AI infers (a deliberate layering decision). |
| `Device_Setup_History` | Hub command confirmations (BigQuery subscription). |
| `Push_Notification_History` | Push dedup/cooldown ledger (written by the listener). |
| `User_Device_Token` | Web-push tokens per installation. |

**risk_id conventions**
| id | Meaning |
|---|---|
| 0 | Safe / informational ("RESIDENT LIKELY SAFE", "AI Coaching Added"). `alert_triggered` distinguishes a real all-clear from a routine ghost record. |
| 99 | **Resident requested help** — the one convention for all voice sources (speaker, Alexa, server endpoint). |
| 999 | Legacy speaker help rows (pre-2026-10-23 listener). Retitled and treated as 99 by api.ashx; no new 999 rows are written. |
| −1 | Synthetic "Hub Offline" row, generated by api.ashx at read time, never stored. |

---

## 6. The dashboard (VivaJot plugin: `api.ashx` + `js/default.js`)

`HandleGetSmartAlerts` (api.ashx) queries `AI_Risk_Scores` (30-day window, max 500 rows)
with display dedup: all open risks, plus only the newest native-safe row and the newest
marked-safe row. Per row it computes:
- **Title**: `CASE WHEN risk_id = 999 THEN 'Resident Requested Help!' ELSE risk_name END`
  (new rows already carry the right name).
- **Marked-safe text**: title + " was marked safe. " + reasoning (so a voice-cleared row
  reads "…was marked safe. … [Cleared by the resident…]").
- **`is_open_help`**: true only for the newest help row (99/999) that no one has marked
  Safe / False Alarm.

`default.js` hero selection is one line: the row with `is_open_help`, else the newest row.
So an open help request **stays pinned in the hero** through the AI's routine scoring
cycles until a caregiver triages it or the resident says "I'm okay" — this fixed the bug
where a "RESIDENT LIKELY SAFE" row two minutes later displaced the emergency. The Event
Log renders the same list in time order and is unaffected by pinning.

`api.ashx` also hosts the older `voice_alert` endpoint (used by Alexa): help → 99
"Resident Requested Help!" score 95; okay → 0 "Resident Likely Safe"; coaching_update →
99 "(Follow-Up)" score 98.

---

## 7. Fleet operations & deployment rules

- **Nightly cycle per hub**: 2:45 AM config-only backup → 3:00 AM restart → on boot, pull
  latest config from this repo's `origin/master` via `ha_updater.sh` (the updater must
  **never overwrite `secrets.yaml`**). ZHA watchdog handles radio failures (bounded host
  reboots).
- **Immediate fleet push**: publish `emergency_update` on `vivajot-setup`
  (see `send_emergency_update.py` pattern); hubs confirm on `vivajot-setup-confirmation`.
- **Who deploys what** (hard rules):
  - HA repo: Claude commits locally only; **Cary pushes** (origin/master auto-deploys to
    the fleet nightly).
  - Google Cloud (BigQuery, Cloud Run, Pub/Sub, GCS): Claude **never deploys or writes**;
    changes are prepared as local files/SQL and Cary applies them (Cloud Run console
    source editor for the listener; BigQuery console for prompt-row INSERTs).
  - VivaJot plugin: deployed with `7.Deploy_Plugins.bat` (note: it ships every pending
    plugin change, not just VivaJot).

---

## 8. Known issues and open design items (2026-10-23)

1. **False wakes auto-send help (ACTIVE, highest priority).** A false "Help Help" wake with
   no following speech leaves the cooldown idle, so `vivajot_voice_help_wake_event` alerts
   unconditionally after 12 s — by design (fail-safe for a resident who can only gasp the
   wake word), but false wakes make it fire erroneously.
   **Approved redesign (2026-10-25, not yet implemented):** see the TO-BE build spec in
   [VOICE_HELP_WORKFLOW.md](VOICE_HELP_WORKFLOW.md). In short: no alert is ever sent
   without the resident saying yes (the wake-only safety net no longer alerts); every
   spoken line is literally true (the hub only knows it sent an alert to the caregiver's
   app); the speaker talks plainly ("I'm here. Do you need help?") and accepts natural
   answers instead of keyword-spotting free speech; "stop" always means be quiet; and the
   cloud keeps one emergency as one incident row.
2. **"Unknown intent HassLawnMowerDock" spoken to a resident.** When speech matches none of
   our conversation triggers, it falls through to HA's default conversation agent, which
   matches against the built-in intents library — including lawn-mower sentences
   (`HassLawnMowerDock`, "dock the lawn mower"). Garbled STT matched one; with no
   lawn_mower entity on the hub the agent spoke the raw error. Fix direction: a custom
   fallback response for unmatched/unavailable intents (ties into the existing "softer
   fallback than 'Sorry, I couldn't understand that'" backlog item).
3. **Alexa "I'm okay" doesn't mark the help row Safe** — it writes a separate risk-0 row,
   so an Alexa help + Alexa okay stays pinned until a caregiver taps Safe. Fail-safe but
   inconsistent with the speaker path.
4. **Hero-pin edge case**: `is_open_help` flags only the *newest* help row; if a caregiver
   marks the newest Safe while an older help row is still open, the older one isn't pinned.
   One-line fix (make the subquery pick the newest *untriaged* help row).
5. **Wake-word robustness**: cutoffs 0.7/0.8 reduce but don't eliminate false wakes
   ("need to stop" scores up to 1.00 on `vivajot`). Real fix is retraining with more
   samples/variants — see [wakewords/README.md](wakewords/README.md).
6. **Per-room targeting**: the wake-event safety net passes `device_id: ""`, so coaching
   falls back to the first satellite; multi-speaker apartments need device resolution.
