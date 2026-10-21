# Reference verification

Every entry in `references_svb.tex` checked against Crossref, the registration
agency for most scholarly DOIs. Recorded here so the bibliography can be audited
without repeating the lookups.

Checked 22 October 2026.

## Why the previous paper's bibliography was not carried over

The template ships 24 entries on pairs trading, cointegration, Kalman filtering
and false-discovery control. They are the sources of that paper's argument, not
this one's. Converting them to APA would have produced 24 references this
manuscript never cites, which §28 of the project instructions prohibits
explicitly. The template was therefore used as a formatting exemplar, and the
reference list rebuilt from the sources this project actually depends on: the 11
in `references/source_register.csv`, the 2024 Basel recalibration the report
already discusses, and the methodological literature the model rests on.

## Verified entries

Journal articles and working papers, each confirmed on authors, year, journal,
volume, issue and pages:

| key | DOI | confirmed |
|---|---|---|
| DiamondDybvig1983 | 10.1086/261155 | J.\ Political Economy 91(3), 401--419, 1983, 2 authors |
| Drechsler2021 | 10.1111/jofi.13013 | J.\ Finance 76(3), 1091--1143, 2021, 3 authors |
| HullWhite1990 | 10.1093/rfs/3.4.573 | Rev.\ Financial Studies 3(4), 573--592, 1990, 2 authors |
| Jiang2024 | 10.1016/j.jfineco.2024.103899 | J.\ Financial Economics 159, 103899, 2024, 4 authors |
| NelsonSiegel1987 | 10.1086/296409 | J.\ Business 60(4), 1987, 2 authors |
| Svensson1994 | 10.3386/w4871 | NBER Working Paper 4871, 1994 |
| Vasicek1977 | 10.1016/0304-405X(77)90016-2 | J.\ Financial Economics 5(2), 177--188, 1977 |

Institutional sources carry no DOI and were verified against the issuing body's
own page: the two Basel standards, the Federal Reserve's SVB review, the four
10-K filings (CIK and accession number in
`references/source_register.csv`), and the three public datasets.

## Corrections the check produced

**REF-008 was incomplete.** The register recorded Jiang et~al.\ as "NBER working
paper w31048 (Journal of Financial Economics)" with no DOI for the published
article. The published version is registered: \textit{Journal of Financial
Economics}, 159, 103899, 2024, DOI 10.1016/j.jfineco.2024.103899. The manuscript
cites the published version. A first bibliographic query that omitted the volume
returned only the working paper and the SSRN preprints, which is worth noting as
a general caution: absence from one query is not absence from the record.

**The 2024 Basel recalibration had no register entry.** `reports/report.md`
discusses the long-shock move to 225bp from 1 January 2026 but cited nothing for
it. The document is \textit{Recalibration of shocks for interest rate risk in the
banking book}, BCBS d578, July 2024, now added. Identified from the Bank for
International Settlements' own publication page.

## The citation-punctuation change APA forces

The template's `\bibpunct{(}{)}{,}{a}{}{,}` renders Quantitative Finance style:
no comma before the year, commas between citations. APA needs
`\bibpunct{(}{)}{;}{a}{,}{,}`. Compiled and checked side by side:

| | template setting | APA setting |
|---|---|---|
| single | (Hull & White 1990) | (Hull & White, 1990) |
| multiple | (Drechsler et al. 2021, Jiang et al. 2024) | (Drechsler et al., 2021; Jiang et al., 2024) |

One residual compromise. APA uses "&" inside parentheses and "and" in narrative
text, but natbib takes a single label per entry and uses it for both, so one
entry cannot do both. The labels use "&", which makes the parenthetical form --
the dominant one in this manuscript -- correct. Narrative mentions are written
out in prose where the distinction matters.

## Two things deliberately not done

**Nelson and Siegel's end page.** Crossref records the start page, 473, only. The
entry gives 473--489, the range the article actually spans. Flagged rather than
silently taken, since the end page is not Crossref-confirmed.

**The Basel PDFs are not stored locally.** The BIS site serves HTML to automated
requests, and that was not worked around. Citation metadata, document numbers
and retrieval URLs are recorded instead, per §22.
