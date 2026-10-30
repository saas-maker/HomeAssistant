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
