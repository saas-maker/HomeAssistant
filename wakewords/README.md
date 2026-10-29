# VivaJot custom wake words (microWakeWord)

Custom on-device wake word models for the Home Assistant Voice PE satellites.
The model runs entirely on the speaker's ESP32-S3 — no audio is streamed off
the device until the wake word fires (important for health-facility privacy).

| File | What it is |
|------|------------|
| `vivajot.tflite` / `vivajot.json` | "VivaJot" — the ONLY wake word in firmware 26.10.28.1+ (cutoff 0.80) |
| `help_help.*` | "Help Help" — retired 2026-10-28 (missed 16% of clean synthetic clips) |
| `somebody_help.*` | "Somebody Help" — trained 2026-10-28, measured, NOT shipped (false-wakes 7/h on conversation) |
| `vivajot_help.*` | "VivaJot Help" — never used |
| `firmware/` | Built firmware images + OTA/flash manifests (what the speakers install) |
| `firmware-config/` | The ESPHome YAML the firmware was built from |

Before shipping any new model, measure it against the deployed one on the same audio
(false accepts per hour on DipCo/CHiME/music, recall on synthetic clips) and ship only if
it is better. The 2026-10-28 numbers are in VOICE_HELP_WORKFLOW.md. The notebook's recipe
(18 Piper voices, stock negatives, 210 music clips, an AudioSet download that is silently
empty) is the root cause of false wakes; the fix is one better-trained model for the whole
fleet, never per-house training.

## Training provenance

*History: this describes the 2026-10-21 `vivajot_help` model, which never shipped. The
shipped `vivajot` model predates it; the 2026-10-28 models are documented in
VOICE_HELP_WORKFLOW.md.*

- Trained 2026-10-21 on Google Colab (free T4) with
  [pafernanr/custom-microwakeword-training-colab](https://github.com/pafernanr/custom-microwakeword-training-colab)
  wrapping [kahrendt/microWakeWord](https://github.com/kahrendt/microWakeWord).
- Phrase variant used for TTS sample generation: `veeva jaht help`
  (chosen by ear over `vivajot help` and `veeva jot help`).
- 900 positive samples: 18 en_US Piper voices x 50 samples, augmented with
  MIT RIRs, AudioSet and FMA background noise; standard microWakeWord
  negative datasets (speech, dinner_party, no_speech).
- 10,000 training steps, negative_class_weight 20, clip 1800 ms.
- Final held-out false-reject rate ~4-6% at low cutoffs; 0 ambient false
  positives during validation. NOT yet validated against real distressed
  speech at distance — do live room testing before relying on it.

## Flashing a NEW speaker (easiest way)

Open the flash page in Chrome/Edge on any machine, plug the speaker in over
USB-C, click Connect -> Install:

    /Plugins/VivaJot/flash.html   (on the VivaJot production site, HTTPS)

The page source lives in the SaasMaker web app at
Plugins/VivaJot/flash.html and always installs the latest firmware from
this repo (it reads firmware/flash_manifest.json via raw.githubusercontent,
so firmware releases need no site changes).

## Updates AFTER the first flash: automatic, over Wi-Fi

Firmware v26.10.22.1+ checks
`raw.githubusercontent.com/saas-maker/HomeAssistant/master/wakewords/firmware/ota_manifest.json`
instead of Nabu Casa's server, so flashed speakers pull updates from this
repo (shows as a normal firmware update in HA; also auto-installable).
To ship new firmware: rebuild, copy the new .bin files here, bump `version`
in `ota_manifest.json` (+ `flash_manifest.json`) and update the md5, push.
The device's "Beta firmware" toggle switches it to `ota_manifest_beta.json`
for staged rollouts. The beta manifest points at its own binary
(`voice-pe-vivajot-beta.ota.bin`), so a beta build never changes what the stable manifest's MD5
expects. 26.10.28.2 (button wake + 8 s listen cap) is on beta; stable is 26.10.28.1.

NOTE: do NOT accept firmware updates on speakers still running stock
Nabu Casa firmware pre-flash; stock updates would keep them on stock.

## Deploying to a Voice PE (manual / ESPHome Builder route)

*History: the normal route is the flash page and OTA above. The `vivajot_help` example below is
kept for reference; that model is not in the firmware.*

1. In HA: ESPHome Builder -> adopt the Voice PE (takeover).
2. In the device YAML add (merge with existing `micro_wake_word` block if the
   adopted config already has one):

   ```yaml
   micro_wake_word:
     models:
       - model: https://raw.githubusercontent.com/saas-maker/HomeAssistant/master/wakewords/vivajot_help.json
   ```

   Or copy both files to the ESPHome config dir and reference the local
   `vivajot_help.json` path instead of the URL. The model is baked into the
   firmware at compile time; the device fetches nothing at runtime.
3. Install (OTA). Then pick "VivaJot Help" in the device's wake word select.

## Tuning

- Misses real voices -> lower `probability_cutoff` (e.g. 0.7) in the JSON and
  re-flash.
- False triggers (TV, conversation) -> raise it (e.g. 0.9), or retrain with a
  higher `NEGATIVE_CLASS_WEIGHT`.
- To retrain or add another wake word: rerun the Colab notebook, edit the
  `CONFIG` dict (phrase, variants), ~30 min end to end.
