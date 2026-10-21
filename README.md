# SVB's Missing EVE

Rebuilding the interest-rate and deposit-run risk of SVB Financial Group and The
Charles Schwab Corporation from public filings alone.

SVB's FY2021 10-K disclosed an economic value of equity of $20,672m and told readers
what a 100bp rate rise would do to it: take 13.9% off. The FY2022 10-K, filed two weeks
before the bank failed, contains no EVE figure at all. This project asks whether an
analyst working only from what SVB had already published at the end of 2021 could have
measured the exposure that later killed it, and applies the same framework to Schwab,
which carried comparable securities losses through 2022 and survived.

## The result

A model calibrated entirely on 31 December 2021 information, with its option-adjusted
spreads then frozen, predicts SVB's held-to-maturity portfolio at **80.36 per $100 of
amortized cost** at 31 December 2022. SVB disclosed **83.41** ($76,169m against
$91,327m) — an error of **−3.7%**, in the conservative direction.

Three further checks, none of them fitted:

| check | model | actual |
|---|---|---|
| Monte Carlo against closed-form Hull–White bond prices | within 0.12bp | — |
| SVB EVE at +200bp (one parameter calibrated, at +100bp) | −27.6% | −27.7% disclosed |
| SVB base EVE level | $21,271m | $20,672m disclosed |

And the finding the project is really for. Solving for the deposit decay that reproduces
SVB's own published EVE sensitivity gives an uninsured-deposit half-life of **3.6
years**. For a base that was 96% uninsured and concentrated in venture-backed
technology, that is an assumption about depositor loyalty rather than a measurement. At
a 2-year half-life the same balance sheet shows −62.5% at +200bp instead of −27.6%.

## What's here

```
config/params.yaml        every modelling choice, with the reasoning beside it
data/raw/                 filing tables extracted to CSV, plus Treasury curves
data/processed/           bucketed balance sheets and model output
references/               the filings themselves, plus the source register
src/data/                 filing extraction, market data, balance-sheet bucketing
src/models/               curves, Hull-White, prepayment, securities, deposits, shocks
src/analysis/             out-of-sample test, EVE scenarios, run risk
tests/                    parser conventions, curve construction, pricing, reconciliation
reports/                  the written report and its figures
```

Modules map onto the pipeline in order: `buckets` (balance sheet), `nss` (curve),
`hull_white` (short rate), `mbs` and `securities` (valuation and OAS), `validation`
(the out-of-sample test), `deposits` (franchise), `shocks` and `eve` (BCBS scenarios),
`run_risk` (the waterfall).

## Reproducing it

Needs Python 3.10 or later and the packages in `requirements.txt`. Network access is
needed once, to fetch the filings from EDGAR and the curves from Treasury and FRED.

```bash
pip install -r requirements.txt
make data        # download filings and market data, extract tables, bucket the books
make test        # 44 tests: parser conventions, curves, pricing, reconciliation
make results     # out-of-sample test, EVE scenarios, run risk
make figures     # the seven charts in reports/figures
make robustness  # re-run the out-of-sample test across assumption variants
```

`make all` runs the lot. The EVE scenario stage is the slow one — it prices four
balance sheets under eleven curves at 20,000 paths each and takes roughly twenty
minutes. Everything else finishes in a couple of minutes.

Results are deterministic: every Monte Carlo draw comes from the seed in
`config/params.yaml`, and the same seed is reused across rate shocks so that
differences between scenarios measure curvature rather than sampling noise.

## Three things worth knowing before reading the numbers

**Par yields are not spot rates.** Treasury publishes par yields. Discounting with them
directly is the standard way a reconstruction like this goes quietly wrong, and the
error grows with maturity and curve slope — exactly where SVB's book sat. Zero rates
here are bootstrapped from the par curve by solving each node so a par bond of that
maturity prices to 100, and the NSS fit is then made against par-yield error rather than
against the bootstrapped zeros. Worst residual is 8.7bp at end-2021 and 10.1bp at
end-2022, both at the 20-year point.

**The Hull–White calibration is historical, not risk-neutral.** Mean reversion and
volatility are estimated from the realised path of the three-month bill, because
swaption surfaces are not free. A historical calibration carries no market price of
interest rate risk and will tend to undervalue the prepayment option embedded in a
mortgage book. It is not corrected for, and the OAS absorbs what is left. The
sensitivity sweep (`make robustness`) shows it is not where the residual error lives:
moving the mean reversion speed or the volatility shifts the headline by a few tenths
of a point against a three-point gap.

**Schwab's disclosed cost basis is not comparable with SVB's.** Schwab moved $206.8bn of
original cost out of available-for-sale during 2022 at $188.6bn of fair value, parking
$18.2bn of loss in AOCI to amortise away. So its securities note shows a book worth
92.07 per $100 when, measured against what it actually paid, the figure is 87.47. SVB
bought its HTM book instead of transferring into it, so no such adjustment applies.
Comparing the two banks without this correction measures an accounting election.

## Limitations

The ones that would change a number, rather than a list of everything imperfect:

- Book yields are used as coupon assumptions. A bond bought at a premium is being
  approximated by a par bond of the same yield. The OAS absorbs the level error; the
  small duration difference it leaves behind is not corrected.
- The prepayment S-curve is stylised, not estimated from pool-level data. Closing the
  gap properly would need loan-level balances, actual WAC and WAM, and an issuer
  prepayment model. The parameters are held fixed across both banks and both dates so
  that nothing can be tuned after seeing the answer.
- Representative maturities stand in for each contractual bucket. The over-ten-year
  bucket is modelled at 20 years for bullets and 25 for pass-throughs. `make robustness`
  re-runs the whole out-of-sample test across thirteen variants of these and the
  prepayment assumptions: the prediction spans 3.6 points and every variant errs in the
  same direction, which is how the residual bias was traced to the bullet approximation
  rather than to the rate model.
- Loans carry no credit spread, since the filings disclose no loan-level fair value to
  calibrate one against. This understates credit risk and does not affect rate
  sensitivity, which is what the EVE measures.
- Schwab publishes yields by maturity bucket rather than by security type, so its
  positions inherit a bucket-level yield. SVB discloses both.
- SVB's published EVE includes a pay-fixed swap book that this project does not model.
  SVB states the effect in the same filing: −15.4% at +100bp before hedging against the
  −13.9% it reported after.

## Data sources

SEC EDGAR for all four 10-K filings, preserved in full under `references/sec_filings/`.
U.S. Treasury for the daily par yield curves. FRED for the short-rate history and the
Freddie Mac mortgage survey. The Basel Committee's 2016 IRRBB standard for the shock
scenarios and the supervisory outlier threshold. `references/references.md` records
every source, what depends on it, and — where a document is not stored here — why not
and how to obtain it.
