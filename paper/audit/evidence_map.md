# Manuscript outline and evidence map

Every number the manuscript may carry, mapped to the frozen file and field it
comes from, and tagged by what kind of claim it can support. Built before any
prose exists, so that the writing is constrained by the evidence rather than
the evidence assembled to fit the writing.

Claim classes used below:

- **O** — observed: a figure disclosed in a filing, or a direct arithmetic
  consequence of disclosed figures.
- **M** — model-dependent: produced by this project's model under stated
  assumptions. Has no disclosed comparator.
- **V** — validated: model output with a disclosed comparator, where the
  comparison is genuinely out-of-sample.
- **C** — calibrated: model output at a point the model was fitted to. Cannot
  be presented as validation.
- **I** — interpretation: the authors' reading. Must be worded as such.

Units are USD millions unless stated. Values are recorded exactly as the
frozen file holds them; rounding happens once, in the LaTeX string, and the
unrounded value stays here.

---

## Structure

Following the previous paper's architecture, adapted to this subject.

| § | Section | Role |
|---|---|---|
| 1 | Introduction | |
| 2 | The two balance sheets being compared | the objects |
| 3 | Research questions and design | |
| 4 | Reconstructing the exposure | the mechanism |
| 5 | Model results: EVE profiles and the outlier test | |
| 6 | The empirical comparison | validation against disclosure |
| 7 | Assumption sensitivity and identification limits | validity threats |
| 8 | Interpretation and limitations | |
| 9 | Conclusion | |

Back matter as in the previous paper: Funding, Disclosure statement, Data
availability statement, Code availability, Declaration of generative AI use.
Three appendices: A curve construction and NSS diagnostics; B Hull-White
calibration and pricing validation; C deposit franchise and the implied
half-life solve.

---

## §2 The objects being compared

Source: `data/raw/rate_sensitivity_disclosed.csv`, `run_risk_summary.csv`.

The disclosure asymmetry is the paper's premise and it is evidenced, not
asserted. `rate_sensitivity_disclosed.csv` holds 13 rows across two banks and
two year-ends:

| claim | value | source | class |
|---|---|---|---|
| SVB FY2021 discloses a full EVE profile | 5 shock points, amounts and percentages | rate_sensitivity_disclosed.csv, bank=SVB as_of=2021-12-31 | O |
| SVB FY2022 discloses NII sensitivity only; EVE fields are empty | eve_amount, eve_change, eve_pct all absent at all four shocks | same, as_of=2022-12-31 | O |
| Schwab discloses no EVE in either year | only nii_pct at +/-100bp | same, bank=Schwab | O |

So SVB FY2021 is the only observation in the sample with a disclosed EVE
profile. That single fact carries the research design: it is the only thing
available to calibrate against and the only thing available to validate on,
and after it the reconstruction is the sole measure that exists.

Balance-sheet scale, all **O**, from `run_risk_summary.csv`:

| field | SVB 2021 | SVB 2022 | Schwab 2021 | Schwab 2022 |
|---|---|---|---|---|
| uninsured_deposits | 182100.0 | 165400.0 | 134800.0 | 68900.0 |
| uninsured_share | 96.245831 | 95.546736 | 30.375548 | 18.787971 |
| book_equity | 16236.0 | 16004.0 | 56261.0 | 36608.0 |
| unrealized_loss_htm | 968.0 | 15152.0 | 0.0 | 14138.0 |
| taint_as_pct_of_equity | 4.710027 | 74.794301 | 0.0 | 30.509779 |

The pairing is the point: at end-2022 both banks carried large held-to-maturity
losses, and the uninsured share differs by a factor of five.

**Correction required.** The repository README states SVB "bought its HTM book
instead of transferring into it, so no such adjustment applies."
`securities_transfers.csv` contradicts this: SVB redesignated 8800.0 at a
132.0 unrealized loss in 2021 (original cost 8932.0). The magnitude is 0.7% of
Schwab's 18228.0, so no conclusion moves, but the manuscript must not repeat
the README sentence. The accurate statement is that SVB's transfer was small
enough to leave its cost basis effectively comparable, while Schwab's was not.

Schwab transfer facts, **O**, from `securities_transfers.csv`:
two tranches, 2022-01 (fair value 108800.0, loss 2400.0, cost 111200.0) and
2022-11 (79800.0, 15800.0, 95600.0); total 188600.0 at 18228.0 loss on
206828.0 of cost, 707.0 amortised back in year, 17521.0 unamortised at
year-end.

## §4 Reconstructing the exposure

Method section. Figures rather than results. Draws on `config/params.yaml`
(frozen, SHA recorded) for every stated assumption, and on the NSS and
Hull-White diagnostics for fit quality, both now persisted — see the Appendix B
block below for the figures and for the two corrections that persisting them
forced.

Deposit parameters, **O** as config facts: beta 0.60 (SVB's own FY2021 Item 7A
assumption), conservative case uninsured half-life 2.0 years and insured 7.0
years.

## §5 Model results

Source: `eve_scenarios.csv`, `outlier_test.csv`.

SVB 2021-12-31 under the disclosure-implied deposit case — the fitted profile.
Base EVE 21271.335262. Note the base level is **not** fitted: only the
half-life is, and only to the +100bp sensitivity. So 21271.335262 against the
disclosed 20672.0 is a genuine 2.9% overstatement, class **V**.

| scenario | delta_eve_pct | class |
|---|---|---|
| parallel_+100bp | -13.900015 | **C** — this is the calibration target |
| parallel_+200bp | -27.607520 | **V** — out-of-sample, against -27.7 disclosed |
| parallel_-100bp | 4.602987 | **V** — against 6.0 disclosed |
| parallel_-200bp | 2.541292 | **V** — against 3.1 disclosed |
| steepener | -22.746056 | **M** — no disclosed comparator |
| flattener | 6.955654 | **M** |
| short_up | 2.527884 | **M** |
| short_down | -2.429294 | **M** |

The down-shock non-monotonicity is the strongest qualitative result and is
**V**: disclosure falls from +6.0 at -100bp to +3.1 at -200bp, and the model
independently falls from 4.602987 to 2.541292. Same direction, same turning
point, not fitted.

The +100bp figure must never be described as agreement. The model was solved
to hit it. `implied_deposit_half_life.csv` records the solve:
disclosed_pct_at_100bp -13.9, beta 0.6, implied_uninsured_half_life 3.589602,
implied_insured_half_life 12.563608, model_base_eve 21271.335262.

Supervisory outlier test, `outlier_test.csv`, all five rows breach the 15% of
Tier 1 threshold:

| bank | as_of | case | worst_delta_eve_pct | loss_over_tier1_pct | class |
|---|---|---|---|---|---|
| SVB | 2021 | implied_by_disclosure | -27.607520 | 36.238743 | V |
| SVB | 2021 | conservative | -62.471290 | 65.430256 | M |
| SVB | 2022 | conservative | -93.238632 | 50.726631 | M |
| Schwab | 2021 | conservative | -89.653875 | 67.858213 | M |
| Schwab | 2022 | conservative | -161.832014 | 43.621917 | M |

Every Schwab EVE figure is **M**. Schwab discloses no EVE, so nothing
validates them. The -161.832014 implies post-shock EVE below zero and needs
wording that states what it is — a model-dependent result under a 2-year
uninsured half-life that Schwab's deposit base does not obviously warrant —
rather than a finding about Schwab's solvency.

## §6 The empirical comparison

Source: `out_of_sample_validation.csv`, `combined_validation.csv`.

The headline test. Model calibrated wholly on 31 December 2021 information,
option-adjusted spreads then frozen, asked to price the same books a year
later.

| bank | class | predicted_price | actual_price | price_error_pct | claim |
|---|---|---|---|---|---|
| SVB | HTM | 80.356036 | 83.407978 | -3.659053 | V |
| SVB | AFS | 89.278254 | 91.143976 | -2.047005 | V |
| Schwab | AFS | 85.719290 | 92.325895 | -7.155744 | V |

Schwab's -7.155744 is not comparable as it stands, because its disclosed cost
basis was rebased by the transfers. `combined_validation.csv` carries the
corrected comparison: predicted 85.719290 against original_cost_price
87.469958 gives error_vs_original_cost -1.750668 and error_pct -2.001450,
against -6.349694 versus the disclosed basis. SVB needs no such correction
(unamortized_transfer_loss 0.0 at 2022-12-31).

Every error is negative. The model is conservative in both banks and both
classifications, which §7 has to account for rather than note in passing.

## §7 Assumption sensitivity and identification limits

Source: `robustness_sweep.csv`, `eve_heatmap.csv`, `run_risk_sensitivity.csv`.

**These are deterministic parameter grids, not sampling distributions.** No
standard error, confidence interval or p-value exists anywhere in the frozen
outputs. The manuscript reports ranges across assumptions and must not use
inferential language — no significance, no precision claims, no error bars
implying sampling variation.

Sweep base case 80.354043 at 4,000 paths. **This is not the headline
80.356036, which is 20,000 paths.** The two must never be interchanged; cite
the sweep base only inside the sweep.

Span across the 13 variants: 78.304158 to 81.904979, so 3.600821 points, and
every variant errs negative (error_pct from -6.112936 to -1.795534). The rate
model is not where the residual lives: mean reversion at a=0.05 moves the
answer +0.277762 and at a=0.15 -0.236104, against a 3-point gap. Prepayment
bounds move it most (-2.049886 and +1.550935). Class **M** throughout.

Run-risk thresholds, `run_risk_summary.csv` and `run_risk_sensitivity.csv`:

| claim | value | class |
|---|---|---|
| SVB 2022 run threshold, base | x_star 0.675, binding liquidity | M |
| SVB 2021 run threshold, base | x_star 0.730, binding liquidity | M |
| realised two-day demand | two_day_run_fraction 0.846433 | O |
| 9 March outflow | outflow_9_march 40000.0 | O |
| two-day demand | demand_over_two_days 140000.0 | O |
| unmet at full run, SVB 2022 | 54042.531579 | M |
| break-even fire-sale discount | 0.282508 | M |
| x_star span across 21 grid cells | 0.2217 to 0.7050 | M |

Solvency never binds in the base case (x_solvency empty for all four
bank-years) but `binding_constraint` takes the value `solvency` in some grid
cells, so the correct statement is that liquidity binds first under the base
assumptions and solvency can bind at high fire-sale discounts. Schwab has
x_star empty and binding `neither` in both years: it crosses no threshold.

## Appendix B: engine diagnostics

Now persisted by `src/analysis/diagnostics.py` into
`pricing_diagnostics.csv` and `curve_fit_diagnostics.csv`, so both are citable.
Running them corrected two figures the write-up had wrong.

Monte Carlo against the closed-form Hull-White price, 20,000 paths, monthly
steps, seed from config, a = 0.088896 and sigma = 0.006187. All **M**, in the
narrow sense that they describe the engine rather than the world:

| maturity | error_bp | standard_error | error in SEs |
|---|---|---|---|
| 1 | 0.029351 | 0.000024 | -0.119632 |
| 5 | 0.016075 | 0.000224 | -0.033653 |
| 10 | 0.008330 | 0.000501 | -0.014177 |
| 20 | -0.084007 | 0.000890 | 0.129516 |
| 30 | -0.118394 | 0.001060 | 0.184198 |

Worst error 0.118394bp against the 5.0bp gate, and the worst discrepancy is
0.184 Monte Carlo standard errors, so the engine sits comfortably inside
sampling noise. **The README's "within 0.4bp" was wrong and has been corrected
to 0.12bp.** The standard error here is simulation precision against a known
analytic value; it is not inference about any economic quantity.

Curve fit, par-yield residual against published Treasury yields:

| as_of | worst residual | at tenor | RMSE | NSS vs bootstrap |
|---|---|---|---|---|
| 2021-12-31 | -8.704993 bp | 20y | 3.875846 bp | 10.882984 bp |
| 2022-12-31 | -10.050728 bp | 20y | 5.385848 bp | 14.961809 bp |

**The README's 7.6bp for end-2021 was wrong and has been corrected to 8.7bp.**
The end-2022 figure and the claim that both sit at the 20-year point were
correct. Both corrections were propagated to `README.md` and
`reports/report.md`.

## §7: the short-rate identification failure

`short_rate_calibration.csv`, from `src/analysis/diagnostics.py`. Twelve rows, the
same Ornstein--Uhlenbeck estimator across four windows and three sampling steps.
Previously this existed only as a print statement in `hull_white.py`, so the
figures the report quoted were not traceable; persisting it fixes that, as with the
other two diagnostics.

Chosen specification, 1990--2022 monthly: half-life 7.7973 years, sigma 61.8651bp,
long-run mean 1.7646\%, $R^2$ 0.0089, 574 observations. Consistent with the
`a = 0.088896` and `sigma = 0.006187` in `pricing_diagnostics.csv`.

The failure that drove the choice, 2003--2022 daily: half-life **364.8202 years**,
long-run mean **86.0726\%**, $R^2$ **0.0000**. The monthly row over the same window
is worse still at 522.7890 years. A configuration guard rejects any fit implying a
half-life beyond 50 years.

The honest extension, which the report did not previously state: the best $R^2$
anywhere in the table is **0.0387**, and the chosen specification's is 0.0089. Mean
reversion is weakly identified under *every* specification tried, not only the
degenerate ones. §7 says so, and pairs it with the sweep result that the reported
quantity barely depends on this parameter -- rate-model variants span 0.920251
price points against 3.600821 for the asset-structure variants.

## §7 addition: the 2024 recalibration, run

`recalibration_comparison.csv` and `recalibration_outlier_test.csv`, from
`src/analysis/recalibration.py`. The USD long shock moves from 150bp to 225bp and
nothing else does, so the long shock reaches only the steepener (weight $+0.90$)
and the flattener ($-0.60$). The parallel and short scenarios carry a long weight
of zero and reproduce to 1.42e-14 percentage points across 45 rows, which is the
guard on the comparison rather than a result.

Steepener, change in EVE, all **M**:

| bank | as_of | deposit case | 2016 | 2024 | move (pp) |
|---|---|---|---|---|---|
| SVB | 2021 | conservative | -48.023804 | -69.207057 | -21.183254 |
| SVB | 2021 | implied_by_disclosure | -22.746056 | -32.587907 | -9.841851 |
| SVB | 2022 | conservative | -66.829778 | -95.038007 | -28.208230 |
| Schwab | 2021 | conservative | -37.821709 | -63.058424 | -25.236715 |
| Schwab | 2022 | conservative | -56.078071 | -96.986044 | -40.907972 |

The finding is a change of identity, not of magnitude. The binding scenario moves
from `parallel_up` to `steepener` in **3 of 5** bank-years, all three of them SVB.
On the disclosure-implied case -- the only one with an external comparator -- the
worst-case loss goes from $-27.607520\%$ to $-32.587907\%$ and the Tier 1 ratio
from $36.238743\%$ to $42.776198\%$. Schwab's binding scenario does not change in
either year, because its parallel exposure ($-89.653875\%$ and $-161.832014\%$)
stays worse than even the strengthened steepener. **No bank-year changes its
outlier-test verdict**: all five breach 15\% of Tier 1 under both calibrations.

Claim boundary: the 2024 calibration applies from 1 January 2026 and post-dates
both valuation dates, so this is a counterfactual about measurement conventions,
not a statement about what either bank's exposure was. It belongs in §7 beside the
other assumption variants and must not migrate into the headline.

## A known inconsistency, quantified and left alone

SVB's 2022 held-to-maturity total is \$91,327m of amortised cost with a \$6m credit
allowance, so \$91,321m of net carrying amount, against \$76,169m of fair value.
The project divides by both:

| where | denominator | disclosed price |
|---|---|---|
| `out_of_sample_validation.csv` (`actual_cost`) | 91,321 net carry | 83.407978 |
| `robustness.py` (`DISCLOSED_HTM_PRICE`) | 91,327 gross cost | 83.402499 |

The model's own `predicted_price` divides by `aged_cost`, which carries no credit
allowance, so gross cost is the marginally more consistent comparator and
`robustness.py` has the better of it. Unifying on 91,327 would move the headline
error from $-3.659053\%$ to $-3.652\%$, a shift of 0.007 percentage points against
a 3.7-point result.

Not changed, because re-running to absorb 0.007 percentage points would invalidate
every frozen figure this map pins and every drafted sentence that quotes one, for no
change to any conclusion. The manuscript states the denominator explicitly instead:
§6 names the \$91,321m carrying amount and the \$6m allowance that produces it, so
the figure cannot be misread as gross cost. Flagged here so the choice is visible
rather than silent.

## Claims the frozen evidence cannot support

To be excluded from the manuscript unless separately authorized:

1. **Any statement of statistical significance, confidence or precision** about
   an economic quantity. Nothing inferential exists; the only standard errors
   in the project are Monte Carlo precision against a known analytic value.
2. **Causal claims about SVB's failure.** The design supports a counterfactual
   measurement claim — the exposure was measurable from FY2021 disclosure —
   not a claim that the disclosure gap caused the failure. Class **I** at most.
3. **Schwab EVE as a measured quantity.** No disclosed comparator exists.
4. **The README's "SVB did not transfer into HTM" sentence.** Contradicted by
   `securities_transfers.csv`.

## Figure provenance

Seven figures, built by `src/plots.py` as byte-reproducible vector PDF
(TrueType embedded, Type 3 avoided) alongside the PNGs the report uses.
`reports/figures/` is the single canonical location; `make paper-figures`
stages the PDFs into `paper/figures/`, which is git-ignored so the same file
is not tracked twice. Rebuilt PDFs are byte-identical, verified.
