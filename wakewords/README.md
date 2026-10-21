# VivaJot custom wake words (microWakeWord)

Custom on-device wake word models for the Home Assistant Voice PE satellites.
The model runs entirely on the speaker's ESP32-S3 — no audio is streamed off
the device until the wake word fires (important for health-facility privacy).

| File | What it is |
|------|------------|
| `vivajot_help.tflite` | "VivaJot Help" — INT8 quantized streaming microWakeWord model (~61 KB) |
| `vivajot_help.json`   | ESPHome model manifest for the above |

## Training provenance

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

## Deploying to a Voice PE

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
