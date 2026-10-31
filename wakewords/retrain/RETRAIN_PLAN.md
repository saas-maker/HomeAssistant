# VivaJot wake word retrain

Goal: a normal home with TV and conversation gets occasional false wakes, a few a day, and real
calls still get through. Zero false wakes is not the goal; that would cost real calls.

Target: at most 1 false wake per hour of continuous talk, and hears at least as well as the deployed
model. The bench picks the most sensitive cutoff that meets the limit.

## How to run

1. Open https://colab.research.google.com, File -> Upload notebook -> `vivajot_retrain.ipynb`.
2. Runtime -> Change runtime type -> T4 GPU.
3. Optional, in Google Drive folder `vivajot_retrain/`: put the talk podcast in `my_audio/`, and phone
   recordings of real people saying VivaJot softly in `real_vivajot/`.
4. Runtime -> Run all. About 4 hours. A paid Colab plan avoids disconnects.
5. The last cell prints PASS or FAIL and saves `vivajot.tflite`, `vivajot.json` and `bench.md` to Drive.

On PASS: the model goes into `wakewords/`, firmware is rebuilt, Cary flashes his speaker and checks
real voices, then the fleet gets it the normal way. On FAIL: nothing ships.

## Baseline: deployed model, firmware 26.10.30.1, measured 2026-10-30

Same rule as the speaker, including VAD. Talk: 3.3 h of earnings calls (`distil-whisper/earnings22`).
At home the same model did about 52/h at 0.90, so this bench shows the problem; 38/h is the right size.

| Cutoff | 0.50 | 0.70 | 0.80 | 0.90 | 0.95 | 0.97 |
|---|---|---|---|---|---|---|
| False wakes per hour | 108.8 | 70.0 | 53.5 | 37.6 | 24.5 | 18.7 |
| okay_nabu control | 20.5 | 10.7 | 6.1 | 2.1 | 0.6 | 0.6 |

Recall on four British voices the model never trained on (32 clips in 3 rooms):

| Voice level | 0.50 | 0.70 | 0.80 | 0.90 | 0.95 | 0.97 |
|---|---|---|---|---|---|---|
| Normal | 86% | 84% | 83% | 82% | 82% | 80% |
| Quiet | 91% | 89% | 88% | 83% | 81% | 81% |
| Soft | 86% | 82% | 82% | 80% | 79% | 78% |
| Very soft | 62% | 58% | 55% | 55% | 46% | 44% |

Voices it did train on score 96-100%, so it has learned those voices and not the word.
No cutoff fixes this: the strictest still false-wakes 19/h, and lowering it barely adds recall.
These are synthetic voices. Real elderly voices have never been measured.

## Simple run, 2026-10-30: FAIL

`vivajot_simple.ipynb`: the old recipe (18 en_US voices x 250, 1500 ms, weight 20, 10k steps) with only
two fixes, household noise present and the end-of-training weights exported. Free T4, about 1 hour.
Bench on 6.72 h of People's Speech talk, 32 unseen British clips in 3 rooms:

| False wakes per hour | 0.50 | 0.70 | 0.80 | 0.90 | 0.95 | 0.97 |
|---|---|---|---|---|---|---|
| deployed | 59.2 | 41.6 | 34.1 | 22.2 | 15.5 | 13.1 |
| simple retrain | 96.8 | 70.8 | 59.0 | 44.6 | 36.0 | 28.6 |

Recall was the same as deployed within a few points at every level (normal 81-85%, very soft 43-59%).
So the two fixes alone do not help: the old recipe's problem is not a missing file, it has no long
talk audio as negatives at all, and more training on the same data makes false wakes worse. The
full recipe (talk negatives and hard-negative mining) is the next step. Note: Colab is now on
Python 3.13, so `piper-phonemize-cross` no longer installs and is not needed.

## Verified 2026-10-31: the chatter IS used in training

Checked with the trainer's own code (microWakeWord 4665173, FeatureHandler) on a fresh Colab machine,
with the deployed recipe's weights (VivaJot 2, speech 10, dinner party 10, no-speech 5):

| Set | Clips loaded | Hours |
|---|---|---|
| chatter: speech (LibriSpeech/VOiCES, 3 mic distances) | 350,535 | 486.9 |
| chatter: dinner party (CHiME-6) | 58,307 | 81.0 |
| noise, no speech (FMA, FSD50K, WHAM) | 238,441 | 331.2 |

Ten real training batches (1,280 samples) drew 40% from speech, 35% from dinner party, 17% from noise,
7% VivaJot. So "the model never heard conversation" is wrong: three quarters of every batch is chatter.
The simple run (same chatter, 5x longer training) doubled false wakes, so neither more chatter nor
longer training is the lever. Untested levers, one per ~1 h run: many more voices saying the word
(hundreds, not 18), sound-alike phrases as negatives, a higher false-alarm penalty, and choosing the
saved snapshot by false alarms on held-out talk. Deployed hears trained voices 96-100% but new voices
82%: it has memorised the 18 voices.

## Test 1, 2026-10-31: 900 voices instead of 18. False wakes nearly gone, hearing halved.

`vivajot_voices.ipynb`: the deployed recipe exactly (900 clips, 1500 ms, weight 20, 10k steps, music
behind the word, best_weights) with ONE change: the 900 clips come from about 900 LibriTTS-R speakers
at several paces instead of 18 Piper voices. Free T4, 55 minutes. Same bench as before.

| False wakes per hour | 0.50 | 0.70 | 0.80 | 0.90 | 0.95 | 0.97 |
|---|---|---|---|---|---|---|
| deployed | 59.2 | 41.6 | 34.1 | 22.2 | 15.5 | 13.1 |
| test 1 (900 voices) | 4.3 | 2.7 | 2.4 | 0.9 | 0.6 | 0.6 |

| Hears a new voice (recall) | normal | quiet | soft | very soft |
|---|---|---|---|---|
| deployed at 0.90 | 78% | 77% | 79% | 49% |
| test 1 at 0.90 | 48% | 47% | 40% | 1% |
| test 1 at 0.50 | 54% | 55% | 47% | 8% |

Verdict FAIL (hears worse than deployed), but this is the first change that moved false wakes at all:
25x fewer at 0.90. The cost is hearing, which halved. With 900 clips over 900 speakers each voice is
heard once, so the model has too few examples of the word. Test 2: same recipe, same 900 speakers,
more clips (about 5,000), to see whether hearing comes back without the false wakes.

## Test 2, 2026-10-31: 900 voices, 5,000 clips. Hears very well, false wakes worse than deployed.

`vivajot_voices2.ipynb`: test 1 with 5,000 clips instead of 900. Same bench.

| | False wakes/h at 0.90 | normal | soft | very soft |
|---|---|---|---|---|
| deployed | 22.2 | 82% | 80% | 45% |
| test 1 (900 clips) | 0.9 | 48% | 40% | 1% |
| test 2 (5,000 clips) | 76.7 | 89% | 86% | 72% |

So more clips restore hearing (the best hearing measured so far, 72% on very soft voices) but false
wakes go to 77/h, worse than deployed. Test 1 and 2 bracket the answer: the word is learnable from
these voices, and the trainer kept the most trigger-happy snapshot because, like the deployed recipe,
it picks the snapshot by hearing alone with no false-alarm check. Test 3: test 2 plus the snapshot
chosen by false alarms per hour on 2 h of held-out talk (the author's recommended mechanism).

## What went wrong before

- **Best model never kept.** With no talk audio to check against, the upstream training code keeps
  the first check, step 2,000 of 10,000. Every model so far has this; the extra steps in the
  10-28 `somebody_help` run never reached the exported file. Read from the code and the 10-21 log;
  the 10-22 log was not available to confirm. The notebook now stops if this happens.
- **Noise missing.** `bal_train09.tar` no longer exists on the server, so AudioSet noise was empty
  in every training.
- **Too little training data.** 900 clips from 18 voices (`veeva jaht`, 1500 ms, weight 20, 10k steps),
  and only 5-second speech chunks as negatives.
- **Firmware logs misread.** "Sliding average" is the value when it first crosses the cutoff, not
  the peak, so 0.71-0.89 did not mean real voices score low.
- **Old bench too kind.** Recall used the peak output, not the 5-window average the speaker uses.
  False wakes used a 2 s gap (the firmware's is about 1 s of quiet) and no VAD.

## The new recipe

| | Deployed | New |
|---|---|---|
| "VivaJot" samples | 900, 18 voices | about 31,000, about 900 speakers plus all en_US voices, slow speech included |
| Noise behind the word | music only | household noise and talk, like a TV on |
| Long talk to learn from | none | about 40 h: People's Speech (includes US hearings), AMI meetings, Common Voice men 60+ |
| Near-miss phrases | none | 18 phrases, negatives only |
| Learn from its own mistakes | none | 2 rounds over about 60 h of unseen talk |
| Picks best model by | nothing (stuck at first check) | false wakes on talk audio it never trained on |

The phrase is unchanged: one phrase, `veeva jaht`, same clip length, same model size.

## Not settled

- **License.** The stock negatives (`kahrendt/microwakeword`) are CC-BY-NC, and upstream calls models
  trained with this data non-commercial. The shipped model already uses them, so this retrain adds no
  new exposure, but it does not remove it. All the added talk data is open for commercial use except
  AudioSet and Earnings-22, whose terms need checking. Replacing the stock sets is a separate job.
- **The notebook has not been run.** Each data source was downloaded and decoded locally, and the
  bench ran for real, but sample generation and training need Colab's GPU and are untested. Any
  failure stops with an error.
- **Real voices.** PASS on synthetic voices is not proof for an 85-year-old. Check real soft voices on
  Cary's speaker before the fleet gets it.
