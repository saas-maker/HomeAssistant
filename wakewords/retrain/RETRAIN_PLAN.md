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
4. Runtime -> Run all. About 4 hours. Free Colab drops the runtime after a while (twice in one afternoon
   on 2026-11-01), and everything on its disk is lost. From test 5 on, the notebooks work inside a Google
   Drive folder: click Allow on the Drive prompt once, and after a drop just Run all again; finished steps
   are skipped. Clicking in the page every 15 minutes keeps the runtime from idling out. Drive is slower
   than local disk (the features step took about 50 minutes instead of 40), and it cannot hold links,
   so the room-echo files are copied instead of linked.
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

## Test 3, 2026-11-01: snapshot chosen by false alarms. False wakes 2/h, hearing worse than deployed.

`vivajot_test3.ipynb`: test 2 plus ONE change, the trainer keeps the snapshot with the fewest false
alarms on 2 h of held-out talk (checked every 1,000 steps). Same bench.

What the trainer printed, false alarms per hour on the held-out talk at each check (steps 1,000 to 10,000):
96, 32, 162, 134, 51, 7.9, 9.4, 29, 78, 199. It kept step 6,000 (7.9/h, hearing 60% on its own test clips).
The number bounces by 20x from one check to the next, so the model is not settling; it is lurching.

| | False wakes/h at 0.90 | normal | soft | very soft |
|---|---|---|---|---|
| deployed | 22.2 | 76% | 75% | 43% |
| test 2 (first snapshot) | 76.7 | 89% | 86% | 72% |
| test 3 (snapshot by false alarms) | 2.1 | 41% | 64% | 4% |

Verdict FAIL: false wakes 10x better than deployed but still above the 1/h limit at every cutoff
(1.5/h at 0.97), and hearing is worse than deployed at every level. The picker works as a picker (it
found the least trigger-happy snapshot) but no snapshot in this run was both quiet and sharp-eared.
Next increments, one change each: a lower learning rate in the second half of training so the model
settles instead of lurching, then more clips only once a settled run exists.

## Test 4, 2026-11-01: slower second half. First model under 1 false wake/h. Hearing 5 points short.

`vivajot_test4.ipynb`: test 3 plus ONE change, the learning rate drops 5x for the second half of training
(verified from the `training_parameters.yaml` the trainer read: learning_rates 0.001, 0.0002). Run on a free
CPU machine (GPU quota was used up); about 1 h training. Same bench. Model saved as
`candidates/test4_vivajot.tflite` (not shipped).

Trainer's own checks, false alarms per hour on held-out talk, steps 1,000 to 10,000:
29, 85, 2.6, 87, 7.1, 24, 12, 20, 6.0, 16. It kept step 3,000 (2.6/h, 98% hearing on its own test clips).
A first run of the same recipe (lost to a disconnect before the bench) gave 6.4, 3108, 3.0, 151, 8.3, 6.4,
13.5, 8.3, 9.4, 16.5 and also kept step 3,000. Both runs: the slow half stays in a band of 6 to 24 instead
of lurching to 200, but the best snapshot still comes early.

| | False wakes/h at 0.90 | normal | quiet | soft | very soft |
|---|---|---|---|---|---|
| deployed | 22.2 | 80% | 80% | 78% | 45% |
| test 3 | 2.1 | 41% | 52% | 64% | 4% |
| test 4 | 0.6 | 75% | 75% | 74% | 10% |

At cutoff 0.85: 0.9/h, hears 75% normal, 75% quiet, 75% soft, 11% very soft.

Verdict FAIL by the rule (hears 3 to 5 points below deployed at normal, quiet and soft), but this is the
first model inside the false-wake limit at any cutoff, and its hearing barely moves with the cutoff
(75% from 0.50 to 0.90), so it is confident on the voices it hears and deaf to the rest. The gap is
very soft voices: 10% against 45%. Next increments, one each: more clips (test 2 showed clips drive
hearing) with the settled training; then quieter, slower clips in the training set for the very soft level.

## Test 5, 2026-11-01: 10,000 clips. Same as test 4. FAIL.

`vivajot_test5.ipynb`: test 4 plus ONE change, 10,000 clips instead of 5,000 (verified from the trainer's
`training_parameters.yaml`: training_steps 5000, 5000; learning_rates 0.001, 0.0002; class weights 1/20).
Three runs were lost to free-Colab drops (two idle CPU runtimes, then the free GPU allowance ran out 10
minutes into training). The run that finished: clips and features generated on a T4, then CPU training
on a fresh runtime from the copies kept in Google Drive. Model saved as `candidates/test5_vivajot.tflite`
(not shipped).

Trainer's own checks, false alarms per hour on held-out talk, steps 1,000 to 10,000:
55, 88, 131, 48, 576, 12, 2.6, 9.8, 24, 20. It kept step 7,000 (2.6/h, 98% hearing on its own test clips).

| | False wakes/h at 0.90 | normal | quiet | soft | very soft |
|---|---|---|---|---|---|
| deployed | 22.2 | 81% | 80% | 73% | 41% |
| test 4 | 0.6 | 75% | 75% | 74% | 10% |
| test 5 | 1.6 | 78% | 78% | 71% | 18% |

Inside the limit, test 5 needs cutoff 0.97: 0.9/h, hears 77% normal, 75% quiet, 70% soft, 10% very soft.
That is test 4's result within a clip or two (96 bench clips, so 1 clip = 1 point). Verdict FAIL by the
rule (3 to 6 points below deployed at normal, quiet, soft).

What this says: doubling the clips did not move hearing. Tests 4 and 5 both land at 75 to 78% normal and
10% very soft once false wakes are held under 1/h. The deployed model's extra hearing comes with 22
false wakes/h; so far every model that is quiet on talk is also 3 to 5 points deafer on normal voices and
far deafer on very soft ones.

Where the very soft gap may come from (theory, not verified): training turns clips down by a random 0 to
45 dB (microWakeWord's default `min_gain_db=-45`), and the bench's very soft level sits at about -49 dB
below full scale, just outside what training ever hears. Next increment, ONE change: training gain floor
-60 dB. If that does not move very soft hearing, the next is real soft-voice recordings on the bench.

Google Drive now holds the test 5 clips and features in `vivajot_test5/`, about 32 GB on Cary's shared
plan. Optional cleanup once a model ships: the `out/` folder and `pos_all/` (the clips, about 1 GB) are
the only parts worth keeping.

## Test 6, 2026-11-02: training gain floor -60 dB. Very soft hearing tripled, normal hearing dropped. FAIL.

`vivajot_test6.ipynb`: test 5 plus ONE change, training turns clips down by up to 60 dB instead of 45
(features made again with `min_gain_db=-60`; verified: the features cell ran 7 minutes instead of being
skipped; trainer yaml unchanged from test 5). Run on the same CPU runtime from the test 5 clips. Model
saved as `candidates/test6_vivajot.tflite` (not shipped).

Trainer's own checks, false alarms per hour on held-out talk, steps 1,000 to 10,000:
35, 3.4, 9.8, 6.0, 132, 7.9, 18.8, 10.5, 48, 0.0. It kept step 10,000 (0.0/h, 98.6% hearing on its own test clips).

| | False wakes/h at 0.90 | normal | quiet | soft | very soft |
|---|---|---|---|---|---|
| deployed | 22.2 | 81% | 80% | 73% | 41% |
| test 5 | 1.6 | 78% | 78% | 71% | 18% |
| test 6 | 0.1 | 52% | 57% | 66% | 15% |

Inside the limit test 6 can run at cutoff 0.50: 0.6/h, hears 64% normal, 65% quiet, 69% soft, 34% very soft.
Verdict FAIL by the rule (normal and quiet 15 points below deployed).

What this says: the gain floor is a real lever for very soft voices (10% to 34%, deployed is 41%), the first
thing that has moved that number. But the kept snapshot is the deafest one, and that is the picker's doing,
not the gain change. Verified in the trainer's `train.py`: its "false alarms per hour" is measured at cutoff
0.5 only, and a snapshot competes on hearing only once it is under `target_minimization` (1.0/h); until
then the trainer keeps whichever has the fewest false alarms. In test 6 only step 10,000 got under 1.0/h, so
it was forced, and its probabilities are so low that even cutoff 0.50 gives 0.6/h on the bench. Steps 2,000
(3.4/h at cutoff 0.5) and 4,000 (6.0/h) were never eligible, though tests 4 and 5 showed a snapshot at
2.6/h at cutoff 0.5 lands at about 1/h on the bench at cutoff 0.90 to 0.97.

Next increment, ONE change: `target_minimization` 3.0 instead of 1.0, so the picker can choose the
best-hearing snapshot among those near the limit and the bench's cutoff does the rest. Same features as test 6.

## Test 7, 2026-11-02: snapshot target 3.0/h. Best model so far: very soft 47%, normal 72%. FAIL.

`vivajot_test7.ipynb`: test 6 plus ONE change, `target_minimization` 3.0 instead of 1.0 (verified from the
trainer's yaml). Same features as test 6 (gain floor -60 dB). Run on the same CPU runtime. Model saved as
`candidates/test7_vivajot.tflite` (not shipped).

Trainer's own checks, false alarms per hour on held-out talk at cutoff 0.5, steps 1,000 to 10,000:
403, 190, 4.5, 77, 6.0, 5.6, 35, 1.5, 27, 35. It kept step 8,000 (1.5/h, 98% hearing on its own test clips).
Again only one snapshot got under the target, so the picker still had no choice.

| | False wakes/h at 0.90 | normal | quiet | soft | very soft |
|---|---|---|---|---|---|
| deployed | 22.2 | 81% | 80% | 73% | 41% |
| test 6 | 0.1 | 52% | 57% | 66% | 15% |
| test 7 | 0.4 | 65% | 69% | 69% | 31% |

Inside the limit test 7 runs at cutoff 0.50: 0.7/h, hears 72% normal, 76% quiet, 74% soft, 47% very soft.
Verdict FAIL by the rule (normal 9 points and quiet 4 points below deployed), but this is the first model
that beats the deployed one on soft and very soft voices while staying under 1 false wake/h. For an
85-year-old with a soft voice this model is better than what ships; for a normal voice it misses about
1 call in 4 where the deployed model misses 1 in 5.

Next increment, ONE change: `target_minimization` 8.0, so steps 3,000 (4.5/h), 5,000 (6.0), 6,000 (5.6) and
8,000 (1.5) all compete on hearing. Risk, stated up front: a snapshot at 5 or 6/h at cutoff 0.5 may not get
under 1/h on the bench at any cutoff; then the bench says so and test 7 stays the best.

## Test 8, 2026-11-02: snapshot target 8.0/h. Closest yet: 4 to 5 points short on normal and quiet. FAIL.

`vivajot_test8.ipynb`: test 7 plus ONE change, `target_minimization` 8.0 (verified from the trainer's yaml).
Same features as tests 6 and 7. Run on the same CPU runtime. Model saved as `candidates/test8_vivajot.tflite`
(not shipped).

Trainer's own checks, false alarms per hour on held-out talk at cutoff 0.5, steps 1,000 to 10,000:
9.4, 49, 284, 31, 9.0, 6.8, 11.3, 10.1, 6.0, 1.9. Four snapshots were under the target this time and the
picker chose by hearing among them, ending on step 10,000 (1.9/h, 98.6% hearing on its own test clips).

| | False wakes/h at 0.90 | normal | quiet | soft | very soft |
|---|---|---|---|---|---|
| deployed | 22.2 | 81% | 80% | 73% | 41% |
| test 7 | 0.4 | 65% | 69% | 69% | 31% |
| test 8 | 1.0 | 77% | 75% | 75% | 44% |

Inside the limit the bench picks cutoff 0.95: 0.4/h, hears 76% normal, 75% quiet, 74% soft, 36% very soft.
Verdict FAIL by the rule (normal 5 points and quiet 5 points below deployed; 1 point is 1 bench clip).
Soft and very soft are better than deployed at every cutoff from 0.75 up.

What this says: letting the picker choose on hearing recovered most of the normal-voice loss (65% to 77%),
and the kept snapshot was the last one, with false alarms still falling (6.0 then 1.9/h). Theory, not
verified: the slow half had not finished settling. Next increment, ONE change: the slow half runs 10,000
steps instead of 5,000 (15,000 total, about 2.5 hours on a free CPU runtime).

## Test 9, 2026-11-02: 10,000-step slow half. Best hearing of any model, but over the false-wake limit. FAIL.

`vivajot_test9.ipynb`: test 8 plus ONE change, training_steps 5,000 then 10,000 (verified from the trainer's
yaml). Same features. Run on the same CPU runtime, about 2.5 hours. Model saved as `candidates/test9_vivajot.tflite`
(not shipped).

Trainer's own checks, false alarms per hour on held-out talk at cutoff 0.5, steps 1,000 to 15,000:
4.9, 4.5, 44, 1.9, 30, 5.3, 11.3, 32, 6.8, 13.5, 10.5, 15.0, 4.1, 15.8, 12.8. It kept step 9,000 (6.8/h): with the
target at 8.0 the picker preferred this looser snapshot for its hearing over step 4,000 (1.9/h).

| | False wakes/h at 0.90 | normal | quiet | soft | very soft |
|---|---|---|---|---|---|
| deployed | 22.2 | 81% | 80% | 73% | 41% |
| test 8 | 1.0 | 77% | 75% | 75% | 44% |
| test 9 | 1.8 | 77% | 78% | 78% | 50% |

At cutoff 0.97 test 9 is at 1.0/h (just over the limit, so the bench picks nothing) and hears 76% normal,
77% quiet, 77% soft, 42% very soft: within 1 to 3 points of deployed on normal and quiet and better on soft and
very soft. Verdict FAIL: no cutoff meets the limit.

What this says: the longer slow half did not settle the false alarms (they still swing 4 to 32/h between
checks), and a target of 8.0 lets the picker choose a snapshot too loose for the bench. Test 8 stays the best
model that meets the limit.

## Where this stands after nine tests, 2026-11-02

Five changes that each moved a measured number, all kept: about 900 synthetic voices instead of 18 (test 1);
the snapshot chosen by false alarms on held-out talk (test 3); a 5x slower second half (test 4); training gain
down to -60 dB so the model hears very soft words (test 6); and a snapshot target of 3 to 8/h so the picker can
choose on hearing (tests 7 and 8). Two changes that did not: 5,000 or 10,000 clips instead of 900 (tests 2 and 5),
and a longer slow half (test 9).

Best candidate: `candidates/test8_vivajot.tflite` at cutoff 0.95: 0.4 false wakes/h (deployed: 22), hears 76%
normal, 75% quiet, 74% soft, 36% very soft (deployed at its own 0.90: 81, 80, 73, 41). It trails the deployed
model by 4 to 5 bench clips out of 96 on normal and quiet voices and beats it on soft ones, with 50x fewer
false wakes. No model has passed the rule as written. The remaining gap is at the size of the bench's own noise
(1 point = 1 clip), so the next evidence that matters is real: flash test 8 on Cary's speaker and listen with
real voices at normal and soft levels, before any more synthetic tuning.

Google Drive folder `vivajot_test5/` (about 32 GB) can be deleted once a model ships; keeping `pos_all/`
(the clips, about 1 GB) saves 15 minutes on a future run.

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
