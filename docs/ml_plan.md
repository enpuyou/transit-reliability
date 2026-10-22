# Machine learning plan: what we could predict, and why

> Superseded on 2026-10-22 by `docs/ml_design.md` (the plan of record). This file keeps the first reasoning.

Written 2026-10-21, before any model is trained. Nothing here is built yet. It says what questions are
worth asking, what information each model would use, why that information should help, and when we have
enough data to start. Plain language on purpose: a "feature" is just one piece of information the model
is allowed to look at.

## Where we stand

- Data collected so far: 8 days (since 2026-10-13). A model needs to see every day of the week several
  times and a mix of conditions. The design asks for at least 3 weeks, so **training can start around
  2026-11-03**. Six weeks is better, and fall rain is not in our data yet (the first week was almost dry).
- What we already have that a model can learn from: for every bus, an estimate of when it reached each
  timepoint with a range; how late it was; how long each stretch between timepoints really takes; how evenly
  buses are spaced; weather by the hour; detour alerts; and the agency's own predictions with their errors.
- The bar to beat: the agency's predictions. Their typical error is about 1 minute at 5 minutes ahead, 1.6
  minutes at 15 minutes ahead and 2.2 minutes at 30 minutes ahead (2026-10-14 to 10-16). A very simple rule,
  "the bus stays as late as it is now" (called persistence), is about as good as the agency at every horizon.
  That tells us how hard the problem is: the easy part (continuing the current delay) is already captured.

## The questions, in order of value

### Question 1: How late will this bus be at a stop 5, 15 or 30 minutes from now?

- **Why ask it:** It is what riders and apps need ("will my bus be late?"), and it is the design's main model.
- **What the model outputs:** the change in lateness between now and the stop. Predicting the *change* (not
  the raw lateness) is easier, because the current lateness is already known.
- **How we judge it:** the average size of its mistakes (in seconds) and the 90th-worst mistake, at each
  horizon, compared with three baselines on the same cases: the timetable (zero delay), persistence, and the
  agency. We also report where it wins or loses: by route, by time of day, in rain vs dry.
- **Kind of model:** gradient-boosted trees (LightGBM). They handle mixed information well and are fast.
  Easy to explain which pieces of information mattered.

Information it can use (all must be known **at the moment of prediction**; see "leakage" below), with why:

| Information | Why it should help |
|---|---|
| Current lateness (from the last stop whose arrival we could already know) | The best single clue; delays tend to persist. |
| How lateness changed over the last 2–3 timepoints | A bus getting later keeps getting later; a recovering one keeps recovering. |
| Scheduled time still to go, and the distance and number of stops left | Longer stretches leave more room for delay to change. |
| Typical running time of the coming segments at this time of day and day type (from our 28-day summary), and how often they run over schedule | Some segments are chronically slow at 5 pm; this is the route's own memory. |
| Is the bus standing still now, its speed, and how old its last report is | Tells us whether it is stuck or moving. |
| The gap to the bus ahead and the bus behind (right now) | Bunching: a bus right behind another tends to run fast, the one in front gets slower and later. |
| How late the **same bus** was on its **previous trip** (the block chain) | The same vehicle runs trip after trip; lateness carries over. Strong for the first stops of a trip. |
| Route, direction, hour, day of week, how far into the trip | The basic shape of the day. |
| Weather now (rain, fog, visibility, temperature, wind) | Rain slows traffic; fog and wind matter less. Weak until we have wet weeks. |
| An active detour or reroute alert on the route | A detour makes the timetable wrong. |
| The agency's own prediction for that stop | Lets the model learn *when the agency is wrong* and correct it. We test with and without it. |
| Later additions: events (games, concerts), road traffic, incidents | Phase 6 sources not yet collected. |

### Question 2: Will these two buses end up bunched downstream?

- **Why ask it:** Bunching is the thing riders feel on busy routes (a long wait, then two buses). If a model
  can say "these two will be bunched three timepoints from now", an agency could hold one back. It is a yes/no
  question, which is simpler to evaluate than a time.
- **Labels:** we already classify every pair of consecutive buses as bunched, gapped, regular or uncertain.
- **Information:** the current gap at this timepoint, how the gap changed over the last two timepoints, scheduled
  gap, the lateness of both buses, time of day, route. A bus that is catching up to the one in front is the clearest sign.
- **Judged by:** how many real bunching events it warns about early (recall), and how many warnings were
  false alarms (precision), compared with the simple rule "the gap is already small".

### Question 3: Is this stretch of road unusually slow right now? (spotting incidents)

- **Why ask it:** Most slowness is normal for the time of day; some is an incident (a crash, a blocked lane).
  A model that knows the normal running time for a segment can flag runs that are far slower than normal
  *for that hour*. This would power a "something is wrong here" marker on the live map.
- **Method:** not a trained predictor; a comparison with the 28-day normal for the same segment, hour and day
  type, flagging runs beyond, say, the 95th percentile when several buses agree.
- **Judged by:** do flagged moments line up with the agency's own alerts about incidents? (It publishes some.)

### Question 4: What explains delay? (understanding, not predicting)

- **Why ask it:** The most useful finding is not "we can predict delay", it is "delays come from X".
- **Method:** simple, readable models (regression) with weather, time of day, route, detours and bunching
  as inputs, and a look at how much each matters after accounting for the others.
- **Caution:** this shows association, not proof of cause. The website says so.

### Question 5 (not machine learning): where is the timetable wrong?

We already have this: segments whose real running time is far from the schedule. A model adds nothing yet.

## How we build it without fooling ourselves

1. **No peeking at the future ("leakage").** Our arrival times are worked out after the fact, using the report
   *after* the bus passed a stop. So when we pretend to be at moment *t*, we may only use arrival estimates that
   were already knowable at *t* (the stop had been passed and the next report had arrived). The
   training table is built "as of t" for every example, and a test checks every information timestamp is not
   after *t*. This is the most common way beginners get a model that looks great and fails in real life.
2. **Split by time, never at random.** Train on earlier days, test on later days. Random splits leak because
   buses on the same trip a minute apart look alike.
3. **Statistics from the training period only.** The "typical running time" for a segment must be computed from
   days before the example, not including it.
4. **Same cases for every comparison.** The model and the baselines are scored on exactly the same rows.
5. **Know the label's limit.** The "truth" is our own estimate (with a range of about a minute). We score only
   examples whose actual arrival is known within 60 seconds, as for the agency comparison, and say that
   errors below roughly 30 seconds are noise.
6. **A promotion rule.** A new model replaces the current one only if it is better on the newest week and
   not worse on any horizon.
7. **Reproducible.** Log the data window, the information list, the settings and the code version
   (MLflow, if Databricks Free Edition allows it: unverified).

## What to build, in order

1. A training table: one row per bus, moment, target stop and horizon, with all the information above as of
   that moment. Check it on the first 8 days now (the table does not need 3 weeks to be built and tested).
2. A baseline scoreboard that reuses what we have (timetable, persistence, agency) on exactly those rows.
3. First model for Question 1 with a small set of information (current lateness, trend, scheduled time to
   go, hour, route). See how much it beats persistence.
4. Add information one group at a time (segment history, bunching, block chain, weather, detours) and record
   what each adds. Often one group does most of the work, and that is the finding.
5. Question 2 (bunching) once the training table exists; it reuses the same information.
6. Question 4 (what explains delay) when there are wet weeks and more days.

## Honest expectations

- Beating the agency by a lot is unlikely: persistence already matches it. A realistic win is a modest drop in
  the larger errors (the 90th-worst case), especially at 15–30 minutes, and a clear explanation of when and why
  the agency is off.
- Three weeks is thin for rain and for events. The model will say little about them until wetter weeks arrive;
  we should say so in its scorecard.
- If a model does not beat persistence, that is a result worth publishing: it measures how predictable
  delay really is with public data.
