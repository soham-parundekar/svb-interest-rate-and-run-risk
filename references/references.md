# Sources

Every source listed here does work somewhere in the project. The register in
`source_register.csv` maps each one to the component that depends on it, and
`references.bib` carries the same entries in BibTeX.

Where a document could be preserved legally it sits in this directory. Where it could
not, the citation, a stable identifier and the retrieval route are recorded instead,
along with the reason it is absent. Nothing here was obtained by working around a
paywall, a login or a bot check.

---

## Primary filings (REF-001 to REF-004)

These are the project's raw material. All four are public SEC filings retrieved from
EDGAR and preserved in full under `sec_filings/`. Filings submitted to EDGAR are public
records and redistributable.

**REF-001** — SVB Financial Group, Form 10-K for the fiscal year ended 31 December 2021.
Filed 1 March 2022. CIK 0000719739, accession 0000719739-22-000023.
<https://www.sec.gov/Archives/edgar/data/719739/000071973922000023/sivb-20211231.htm>
Local: `sec_filings/2021_SEC_SVBFinancialGroup_10K.htm`

The starting point for everything. Supplies the securities note (AFS and HTM by type,
with amortized cost, gross unrealized gains and losses, fair value and the credit
allowance), the contractual maturity distribution with weighted-average yields, the
deposit note, the loan maturity table split fixed against variable, and Item 7A — which
in this filing still contains the EVE sensitivity table the project is named after.

**REF-002** — SVB Financial Group, Form 10-K for the fiscal year ended 31 December 2022.
Filed 24 February 2023. CIK 0000719739, accession 0000719739-23-000021.
<https://www.sec.gov/Archives/edgar/data/719739/000071973923000021/sivb-20221231.htm>
Local: `sec_filings/2022_SEC_SVBFinancialGroup_10K.htm`

The validation target. Discloses HTM securities at $91,327m amortized cost against
$76,169m of fair value, which is the number the end-2021 model has to predict. Its
Item 7A reports NII sensitivity in percentage terms only and contains no EVE figures;
that absence is established by reading both filings side by side rather than asserted.

**REF-003** — The Charles Schwab Corporation, Form 10-K for the fiscal year ended
31 December 2021. Filed 24 February 2022. CIK 0000316709, accession
0000316709-22-000009.
<https://www.sec.gov/Archives/edgar/data/316709/000031670922000009/schw-20211231.htm>
Local: `sec_filings/2021_SEC_CharlesSchwabCorp_10K.htm`

The comparison bank at the base date, holding $391bn of available-for-sale securities
and no held-to-maturity book at all. Supplies the securities and maturity tables, the
bank deposit note, the uninsured deposit figure and the net interest revenue simulation.

**REF-004** — The Charles Schwab Corporation, Form 10-K for the fiscal year ended
31 December 2022. Filed 24 February 2023. CIK 0000316709, accession
0000316709-23-000009.
<https://www.sec.gov/Archives/edgar/data/316709/000031670923000009/schw-20221231.htm>
Local: `sec_filings/2022_SEC_CharlesSchwabCorp_10K.htm`

Documents the two 2022 transfers out of available-for-sale, at fair values of $108.8bn
and $79.8bn carrying net unrealized losses at transfer of $2.4bn and $15.8bn. Those
figures are what make Schwab's disclosed HTM amortized cost incomparable with SVB's
without adjustment, and the project's Schwab result turns on them.

---

## Regulatory and official sources (REF-005, REF-006)

**REF-005** — Basel Committee on Banking Supervision (2016), *Interest rate risk in the
banking book*, Standards, April 2016, BCBS publication d368.
<https://www.bis.org/bcbs/publ/d368.htm>
Not preserved locally. See the note on redistribution below.

Defines the six standardised shock scenarios and the 15% of Tier 1 supervisory outlier
test implemented in `src/models/shocks.py` and `src/analysis/eve.py`. The USD
calibration used here — 200bp parallel, 300bp short, 150bp long, with a four-year decay
and a post-shock floor — is the 2016 one, which is the calibration in force at both of
this project's valuation dates.

The Committee recalibrated these shocks in 2024, raising the USD long-rate shock from
150bp to 225bp with effect from 1 January 2026. That revision post-dates the period
analysed here, so it is not the base case; it is run as a robustness check and reported
separately. The 150-to-225 change was confirmed against a published summary of the
recalibration (KPMG China, *Recalibration of shocks for IRRBB*, July 2024,
<https://assets.kpmg.com/content/dam/kpmg/cn/pdf/en/2024/07/recalibration-of-shocks-for-irrbb.pdf>)
rather than taken from memory.

*Why the document is not stored here.* bis.org serves an HTML landing page in place of
the PDF to automated requests. Defeating that would mean circumventing an access
control, which this project does not do. The standard is freely available to a person
visiting the page above.

**REF-006** — Board of Governors of the Federal Reserve System (2023), *Review of the
Federal Reserve's Supervision and Regulation of Silicon Valley Bank*, April 2023.
<https://www.federalreserve.gov/publications/files/svb-review-20230428.pdf>
Local: `official_reports/2023_FederalReserve_SVB_Supervision_Review.pdf`

The supervisory account of what happened, used for the framing of the failure and for
the realised deposit outflow of roughly $42bn on 9 March 2023 that Module G overlays on
its modelled threshold. A work of the United States Government and in the public domain.

---

## Academic literature (REF-007, REF-008)

**REF-007** — Drechsler, I., Savov, A. and Schnabl, P. (2021), "Banking on Deposits:
Maturity Transformation without Interest Rate Risk", *The Journal of Finance*, 76(3),
1091–1143. DOI: 10.1111/jofi.13013.
Not preserved locally; published version is under Wiley copyright.
Author manuscript: <https://pages.stern.nyu.edu/~pschnabl/research/DSS_2021_JF_MT.pdf>
Working paper: NBER w24582, <https://www.nber.org/papers/w24582>

The theoretical case that this project tests. Deposit franchises hedge interest rate
risk because deposit rates are low and insensitive to market rates, so a bank that lends
long at fixed rates is matching, not mismatching. Module E implements exactly that
mechanism, and the heatmap in Figure 5 is the test of its boundary condition: the hedge
is paid for by the assumption that the deposits stay.

**REF-008** — Jiang, E. X., Matvos, G., Piskorski, T. and Seru, A. (2024), "Monetary
Tightening and U.S. Bank Fragility in 2023: Mark-to-Market Losses and Uninsured
Depositor Runs?", *Journal of Financial Economics*.
Not preserved locally; published version is under Elsevier copyright.
Working paper: NBER w31048, <https://www.nber.org/papers/w31048>

Establishes the interaction this project models bank by bank: mark-to-market losses
matter for solvency in proportion to how much of the funding base is uninsured and can
therefore run. Motivates the two-layer deposit split in Module E and the waterfall in
Module G.

---

## Market and economic data (REF-009 to REF-011)

**REF-009** — U.S. Department of the Treasury, Daily Treasury Par Yield Curve Rates.
<https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve>
Local: `../data/raw/treasury_par_curves.csv` (retrieved 2021 and 2022 series)

The input to Module B and therefore to every discounted value in the project. These are
*par* yields, which is the single most important thing about them: the bootstrap in
`src/models/nss.py` exists because feeding them into a discounting routine as though
they were spot rates is the standard way this kind of reconstruction goes wrong. Public
domain as a work of the United States Government.

**REF-010** — Federal Reserve Bank of St. Louis, FRED series DGS3MO, DGS10, DFF.
<https://fred.stlouisfed.org/>
Local: fetched on demand by `src/data/market_data.py`; not committed, since the series
are reproducible from the script and run to thousands of rows.

DGS3MO is the short rate the Hull-White mean reversion and volatility are estimated
from. DGS10 anchors the mortgage rate proxy. DFF was used to check that the mean
reversion estimate is not an artefact of the particular short-rate series chosen; it is
not, and the comparison is reported by `calibration_sensitivity()`.

**REF-011** — Freddie Mac Primary Mortgage Market Survey, 30-year fixed rate, via FRED
series MORTGAGE30US.
<https://fred.stlouisfed.org/series/MORTGAGE30US>
Local: fetched on demand by `src/data/market_data.py`.

Sets the level of the mortgage rate in the prepayment model. The spread of this rate
over the model's own ten-year zero is anchored at each valuation date rather than
averaged, because it moved from 1.59% to 2.59% across 2022 and averaging it would put
the end-2022 mortgage rate 75bp too low — which would flatter the out-of-sample result
the project is judged on.

---

## A note on what is deliberately absent

No source here was retrieved by circumventing an access control, and nothing is cited
that was not read. Two things that might be expected are missing on purpose:

*First Republic Bank and Signature Bank.* Both were state-chartered banks that reported
to the FDIC rather than filing periodic reports with the SEC, so neither has 10-K
filings on EDGAR. They are not used, and are not presented as if they were.

*Swaption-implied volatilities.* The Hull-White calibration here is historical, not
risk-neutral, because implied volatility surfaces are not free. The consequence is
stated in the report rather than hidden: a historical calibration carries no market
price of interest rate risk and will tend to undervalue the prepayment option.
