# Defensibility Record

Every design decision in this project, the challenge an interviewer or reviewer
would raise against it, and the answer. If a decision is not defensible, that is
stated rather than hidden.

Use this to rehearse. If you cannot give the answer in your own words without
reading it, redo that step.

---

## Step 1 — Framing: why this project exists at all

**Decision.** The EY engagement solved this problem once, by hand. This
repository is the automated version of it, built so any company with the same
problem can run it continuously instead of commissioning a study.

**The one-sentence version, for an interview.**
"I did this as a one-time consulting project at EY, realised the answer goes
stale within a few quarters because suppliers and demand keep moving, and built
the pipeline version so it recomputes itself on a schedule."

**Challenge: "Isn't this just a portfolio exercise?"**
No. The problem was real: a client running a flat 25-day cover rule across ten
distribution centres. The method sized excess working capital at 98% service
across 150 location-SKU pairs over SAP extracts, and the client piloted it. But
it was a deliverable, not a system, and that is exactly the gap this fills.

**Challenge: "Why automate it? The study already gave them the answer."**
Because the answer decays. Buffer parameters are computed from supplier
behaviour and demand variability, and both drift. Within a few quarters the
numbers are wrong again and the client is back to two bad options: live with
it, or pay for another study. Most companies running a flat cover rule will
never hire anyone to look at it in the first place. A pipeline that runs nightly
removes the analyst from the loop entirely, which is the only version of this
that scales past one client.

**Challenge: "What specifically makes this a system rather than a report?"**
Four things, all verifiable in the code. Idempotent loads, so a rerun or a
retry cannot corrupt. Quality gates that fail the run before bad numbers reach
a planner, because nobody is reviewing the output by hand. A `calc_date` key, so
every run writes a dated snapshot and the table becomes a history of how each
parameter moved. And an orchestrator with retries and backfill, so a briefly
unreachable source is not a missed day.

**Challenge: "Did the client actually save that money?"**
No, and I would not claim that. The figure is opportunity sized and validated
against the client's own data at a fixed 98% service level, approved as a
business case and piloted the following quarter. Realised savings depend on
execution I was not there for.

**Challenge: "Why not just describe the EY work?"**
Because nobody can audit a story, and because the EY work is the manual version.
A reviewer can clone this, run `make all`, and check every transformation. That
is the part the engagement could not produce.

---

## Step 2 — Dataset choice

**Decision.** Olist Brazilian e-commerce over M5/Walmart.

**Challenge: "M5 is bigger and better known. Why not that?"**
M5 has 59M rows of genuine demand but no supplier, no purchase order and no
delivery timestamps. Safety stock needs both demand variability and lead-time
variability. M5 supports only one half of the calculation, and it is the half
that matters least: at EY, roughly 95% of the required buffer traced to lead
time rather than demand.

**Challenge: "So Olist has real lead times?"**
It has real delivery durations. Whether they are the *right* duration is Step 3,
and the answer is no.

---

## Step 3 — The proxy, and its honest limit

**Decision.** Use order-placed-to-customer-delivered as a proxy for
replenishment lead time, and say so in the README's third section rather than in
a footnote.

**Challenge: "That's outbound fulfilment, not inbound replenishment. Your
formula expects the wrong quantity."**
Correct, and it is the first thing I raise. Safety stock protects against
variability in the time between ordering from a supplier and receiving goods.
Olist measures the time between a customer ordering and receiving goods. Same
arithmetic, opposite direction, different business meaning.

**Challenge: "Then why is the project worth anything?"**
Two reasons. The engineering is direction-agnostic: idempotency, layering, SCD2,
quality gates and the variance decomposition are identical whichever duration
feeds them. And the EY engagement used the correct quantity, so the method is
demonstrated on real replenishment data and the implementation is demonstrated
on public data. Neither claim rests on the other.

**Challenge: "Why not find a dataset with real replenishment lead times?"**
I looked. Public datasets with paired purchase-order and goods-receipt
timestamps essentially do not exist, because that data is commercially
sensitive. Sourcing one is listed as future work.

---

## Step 4 — Raw layer: all columns typed as text

**Decision.** Every raw column is `text`, including timestamps and numerics.

**Challenge: "That's lazy typing."**
The opposite. If `order_delivered_customer_date` is declared `timestamp`, `COPY`
rejects malformed rows and the load either fails or silently skips them. Raw
must accept the source exactly as sent so defects are visible and countable.
Casting happens in staging, where a bad value becomes an inspectable row rather
than a load failure.

**Challenge: "You kept a misspelled column name."**
`product_name_lenght` is misspelled in the source file. Renaming it in raw
breaks the guarantee that raw mirrors the file byte for byte. It is renamed in
staging, which is the correct layer for it.

---

## Step 5 — Three layers rather than transform-on-load

**Challenge: "Why not clean during ingestion and skip a layer?"**
Because a bug in cleaning logic would then be permanent. With raw immutable, a
cleaning error costs a rerun of staging; without it, it costs a re-collection
that may be impossible. The same reason incoming inspection is separate from
receiving: correcting a defect at the dock without recording it destroys the
supplier quality signal.

---

## Step 6 — Profiling before cleaning

**Decision.** Eight queries run against raw before a single row is modified.

**Challenge: "How did you pick those eight?"**
Six are universal: row counts, nulls, categorical distributions, volume over
time, duplicates, numeric ranges. Two came from the formula itself. I wrote down
what `SS = Z × sqrt(LT × σ_D² + D̄² × σ_LT²)` assumes, then wrote one query per
assumption: lead time must be computable, lead time must be positive, demand
must have enough observations for a standard deviation, zero weeks must be real
rather than missing, and the period must be representative.

**Challenge: "Did the profile change anything?"**
Four of five assumptions failed and each forced a decision. One was fatal to the
original plan, which is Step 7.

---

## Step 7 — Grain

**Decision.** One row is one category, one seller, one week.

**Challenge: "Why not per product? That's the real planning unit."**
Because it is not computable. 32,951 products carry 112,650 order lines. The
median product sold **once** in 21 months and 18,117 sold exactly once. A
standard deviation needs multiple observations; one data point has no spread.
Per-product safety stock is undefined for over half the catalogue.

**Challenge: "So you aggregated to make the maths work."**
I aggregated to the level where the signal exists, which is also how planners
genuinely manage long-tail items: by family, not individually. The top decile of
products carries 53.8% of sales and 8.1% of the catalogue covers half of it.
That shape forces family-level management in real systems too.

**Challenge: "What do you lose?"**
Within-category mix. A seller strong in one SKU and weak in another reads as
average. Stated in the README as a known limitation.

---

## Step 8 — Keeping zero-demand weeks

**Decision.** Generate a full calendar spine per series, then left-join demand.

**Challenge: "Why not just aggregate the sales you have?"**
Because a week with no sales is an observation, not a missing row. Dropping
zero weeks raises the apparent mean and lowers the apparent standard deviation,
which under-buffers precisely the erratic items that need the buffer most. This
is one of the most common silent errors in demand statistics.

---

## Step 9 — Exclusions, counted

**Decision.** Every excluded row is written to `staging.exclusions` with a count.

**Challenge: "You dropped 2,963 orders. How do I know that didn't bias the
result?"**
You do not have to take my word for it; the counts are in a table. And the
direction of the bias is the reason for the decision: undelivered orders have no
observable lead time, and the sellers who never delivered are plausibly the
worst performers. Excluding them makes reliability look *better* than it is, so
the exclusion is conservative in the wrong direction and must be visible.

**Challenge: "Why trim 2016?"**
September 2016 has 4 orders, October has 324, November has none at all, December
has 1, and January 2017 jumps to 800. That is a platform launching. Including it
reads as extreme demand variability that never happened.

---

## Step 10 — SCD Type 2 on the seller dimension

**Challenge: "Why keep history instead of overwriting?"**
Because a planner will eventually ask why last quarter's number differed. If the
seller row was overwritten when their profile changed, that question is
unanswerable and trust in the system is gone. Type 2 keeps `valid_from`,
`valid_to` and `is_current`, so December's parameters remain reproducible after
a January change. 3,095 sellers produce 4,018 dimension rows, and the surplus is
exactly the history.

---

## Step 11 — Transform functions never touch the database

**Challenge: "Why the separation? It's more code."**
Testability. `compute_safety_stock` takes DataFrames and returns a DataFrame, so
six unit tests run with no Postgres anywhere. If it opened a connection, testing
the maths would require standing up a database, and in practice that means the
maths goes untested.

---

## Step 12 — Idempotency

**Decision.** `ON CONFLICT (calc_date, category, seller_id) DO UPDATE`.

**Challenge: "Prove it."**
Run `make run` three times and count rows. 951 each time.

**Challenge: "But the table grew when you changed calc_date."**
Correct, and deliberate. Idempotency is *per calc_date*. The primary key
includes it, so rerunning a date upserts while a new date adds a snapshot. The
table is a time series of parameter history, which is what answers "why was the
number different last quarter". Saying "it's idempotent" without that
qualification would be imprecise.

**Challenge: "Why does it matter?"**
Every retry and every backfill depends on it. Without idempotency, a task that
fails halfway and retries corrupts the data, and reprocessing history is
impossible.

---

## Step 13 — Quality gates before the load

**Decision.** Four assertions run between compute and load, and failing any one
fails the run.

**Challenge: "Why before the load, not after?"**
Because publishing a bad number and retracting it costs more than failing. Once
a planner has acted on a wrong buffer, the damage is physical.

**Challenge: "Why compare row count to the previous run instead of a fixed
threshold?"**
A fixed threshold cannot catch a source file that silently halved. A
relative check can. Note that the original implementation of this check compared
against the whole table rather than the latest snapshot, so its baseline drifted
and it would never have fired. That is a real bug, found and fixed, and it is
worth saying out loud: a volume check that moves its own baseline passes forever
and catches nothing.

---

## Step 14 — Service levels by ABC class

**Decision.** A 98%, B 95%, C 90%.

**Challenge: "Where did those numbers come from?"**
They are conventional defaults, not derived from this data, and I would not
present them otherwise. A real deployment sets them from stockout cost against
holding cost per class. What *is* defensible is tiering rather than applying one
level flat, because the cost of service level accelerates: 90 to 95 costs 29%
more buffer, 95 to 99 costs another 41% on top.

**Challenge: "You used revenue for ABC, not cost."**
Correct, because Olist has price but no cost. For *ranking* the two usually
agree closely. For the holding-cost tradeoff they do not, because holding cost
is a percentage of cost and stockout cost is margin. I would use revenue to
segment and refuse to use it to set service levels against a capital charge.

---

## Step 15 — Intermittent demand held back

**Decision.** 576 of 951 series flagged as >40% zero weeks and excluded from the
published parameters, with a reason code.

**Challenge: "You excluded 60% of your data. Isn't that the project failing?"**
It is the project being honest. The formula assumes normally distributed demand.
Intermittent series are closer to compound Poisson, and fitting a normal to them
yields numbers that look authoritative and are wrong. Publishing them would be
the failure. Croston's method is the correct treatment and is named as future
work.

---

## Step 16 — The headline findings

**Challenge: "Your promised lead time has almost the same standard deviation as
observed. Doesn't that undercut the finding?"**
It changes it, and I had an earlier hypothesis that the data overturned. I
expected the promise to be a flat rule with near-zero variance, which would have
*under*-buffered. It is not flat; it varies by route, 7.60 days against 8.21.
The divergence is in the mean: 23.6 promised against 11.8 observed, padded
2.07x. So planning on the promise *over*states inventory by 43.5%, and most of
that is cycle stock because cycle stock scales linearly with lead time while
safety stock scales with its square root.

**Challenge: "What's the strongest finding?"**
That a flat two-week cover rule under-buffers 201 of 201 A-class series and
over-buffers 32 of 56 C-class. Unanimous, not mixed. It fails in direction
because it is indexed to average demand and blind to variability, and 188 of 201
A-class series have a coefficient of variation above 0.5. That is a
misallocation finding and it is invisible without computing per series.

**Challenge: "At EY you said 95% of the buffer came from lead time. Here it's
49%. Which is it?"**
Different datasets measuring different things. EY used genuine replenishment
lead times across Indian distribution centres; this uses outbound fulfilment on
a Brazilian marketplace. I do not present the second as a reproduction of the
first. What is common is the structure: a large share of required buffer traces
to supply-side variability rather than demand, which means better forecasting
attacks the smaller half.

---

## Step 17 — Orchestration

**Challenge: "Why Airflow instead of cron?"**
Cron has no dependency graph, so a failed ingest still lets transform run on
stale data and publish numbers someone acts on. No retries, so a briefly
unreachable source becomes a missed day. No backfill. No per-step visibility, so
you learn the job failed rather than which gate failed.

**Challenge: "What is execution date?"**
The logical date a run represents, not the wall clock time it ran. A run stamped
2018-09-01 computes parameters as of that date whenever it executes. That is
what makes backfills meaningful, and it is why `calc_date` comes from the DAG
context rather than `date.today()`.

**Challenge: "Did you actually run it, or just write the DAG?"**
Ran it. All four tasks (`ingest_raw`, `compute_parameters`, `validate_quality`,
`load_parameters`) executed to success on Airflow 3.1.8 and the run wrote its
snapshot keyed on the logical date. The DAG imports `PythonOperator` from both
the Airflow 2 and Airflow 3 paths so it runs unchanged on either.

**Challenge: "Why parquet instead of XCom for the DataFrame?"**
XCom is for small messages and is stored in the metadata database. Pushing a
951-row frame through it bloats that database and is the standard anti-pattern.
The frame goes to a run-scoped file and only the path travels through XCom.

---

## Known weaknesses, volunteered

Say these before being asked. Volunteering a limitation is the strongest move
available; having one found for you is the weakest.

1. The lead time is outbound fulfilment, not inbound replenishment.
2. There is no inventory position, so the model is unvalidated. Backtesting
   against real stockouts would need on-hand history.
3. Service levels are conventional defaults, not derived from cost.
4. ABC uses revenue because cost is unavailable.
5. The formula assumes demand and lead time are independent; they are not.
6. 60.6% of series are unmodelled.
7. Units are order lines, not currency, so no value-weighted comparison exists.

---

## Step 18 — The planner's audit, and what it caught

Before publishing, the pipeline was reviewed the way a planner would review any
new parameter set: not by reading the code, but by asking whether the numbers
could be acted on. Six problems came out of it. All six are fixed; they are
recorded here because being able to say what your own review caught is worth
more than claiming it was right first time.

**1. The baseline comparison was apples to oranges, and it changed a headline.**
A flat "two weeks of cover" rule is a TOTAL on-hand policy. It was being
compared against safety stock alone, which is only part of the target. On the
like-for-like basis (naive total vs cycle plus safety) the flat rule is short on
every single published series. The claim that C-class was "about right at
-0.3%" was an artefact of the mismatch; like for like it is 38% short. The
direction finding survives and is still real (A is 60% short against C's 38%),
but it is a gradient, not a sign change. `test_naive_baseline_is_a_total_cover_policy`
now guards this.

**2. `calc_date` claimed a freshness the data did not have.**
Parameters were stamped 2018-10-31. The last delivered order in the dataset is
2018-08-29. The default is now 2018-08-31.

**3. Twelve percent of the published population was dead.**
46 of 375 series had not sold in six months, and one had not sold in eighteen.
They were being published as current buffer recommendations. There is now an
`activity_window_weeks` setting (13); series with no sale inside it are flagged
`is_stale` with a reason code and excluded from the published set. The oldest
published series has now sold within 12.6 weeks.

**4. The trailing window was anchored to the wrong thing.**
"Last 26 weeks" meant each series' own last 26 weeks, so one snapshot mixed
parameters computed from 2017 data with parameters computed from 2018 data.
Every number in a snapshot must describe the same period. The window is now
anchored to `calc_date`.

**5. ABC ranked partly on how long a series had existed.**
It summed revenue over each series' full history, and A-class series averaged 58
weeks of history against C-class 32. The column was also named `annual_revenue`
when it was neither annual nor a fixed period. It now ranks on revenue over the
same anchored window, annualised, under the name `annualised_revenue`.

**6. Buffers are sensitive to single-week spikes.**
Roughly 13% of the average series' demand standard deviation comes from its
single largest week, and 732 of 951 series had a week at four times their mean.
This one is *documented rather than fixed*, because the right treatment is a
judgement call: winsorising, a seasonal decomposition, or leaving it and
reviewing A-class manually are all defensible, and picking one silently would
be worse than naming the exposure. `sql/030_findings.sql` reports the figure on
every run.

**Challenge: "Why should I trust the rest if you found six problems?"**
Because the alternative is a reviewer finding them. Five were corrected and are
covered by tests; the sixth is quantified and reported on every run. A parameter
set nobody has tried to break is not trustworthy, it is just untested.

---

## Step 19 — The second audit, read as a planner

A second pass after the first corrections, asking what a planner would query
before trusting the output. Four more problems, all fixed.

**1. Demand was counted in order lines, not units.**
`fct_demand_weekly` used `count(*)`, so every line contributed exactly one
unit. That is correct for this dataset, which writes one row per unit, and
wrong for essentially any real purchase order line, which carries a quantity.
The repository claimed in USAGE.md that another company could point it at their
own extract, and for any of them with a quantity column the demand, variability
and buffers would all have been understated with no warning. `quantity` is now
a mapped column with an explicit one-unit fallback, and the fallback is stated
in the SQL rather than hidden inside an aggregate. Verified by running an
extract with quantity set to 3 on every line: demand scales as expected, and
more series clear the minimum-units floor, which is also correct.

**Challenge: "Why was quantity optional at all?"**
Because the demo dataset genuinely has no quantity column. Making it required
would have blocked the dataset the project is built on. The honest treatment is
an optional field with a loudly documented default, not a silent assumption.

**2. `min_weeks_history` was dead configuration.**
The value 8 was hardcoded in `sql/020_marts.sql` while `config.yaml` advertised
it as a setting. Changing the config did nothing. Both series minimums now
travel through `raw.pipeline_config`, the same mechanism already used for the
completed-status value. A knob that does not turn is worse than no knob,
because someone will turn it and believe the result.

**3. The architecture diagram misstated the orchestrator's scope.**
It showed Airflow triggering the compute step, when the DAG's first task is
`ingest_raw`. The arrow now goes to ingest. Caught by reading the diagram
against `dags/buffer_params_dag.py:115` rather than against memory.

**4. `ss_baseline` duplicated `naive_total_cover`.**
A leftover from the first audit's rename, still shipped in the output CSV. Two
columns with identical values and different names is how a downstream user
picks the wrong one. Removed.

**What the diagram's gates caught, for the record.**
The diagram is generated by Archify with repository evidence, and its gates
rejected three things before it rendered: two card colours outside the allowed
set, a missing git origin, and a citation to `sql/020_marts.sql` lines 17 to
103 in a file that has 99 lines at that revision. Every `file:line` on the
diagram is verified against committed bytes at the pinned commit, so a
plausible-looking citation that does not exist fails the build.
