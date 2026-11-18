# BESS productivity model — Requirements, assumptions and logic

Summary of everything defined together during the development of the model: what it had to
compute, which assumptions were used, how it works internally, and what stays out of scope.
This is the single reference to keep alongside the model files — and, since the output
workbook no longer carries its own assumptions sheet, **this document is now the definitive
record of every parameter, default and modelling choice**. Updated to **v7** (see §11-ter; v6
is §11-bis, v4's simplification pass is §11).

---

## 1. Goal of the model (original request)

A tool that, given:

- an hourly price curve covering the 20 operating years 2029-2048,
- the BESS's technical characteristics — power, energy (or duration), charge/discharge
  efficiency, capacity degradation and RTE over time,

returns the **BESS's revenue**, with the tool itself deciding when to charge and when to
discharge based on prices. The goal stated from the start was twofold: correctness of the
calculation and **usability** — a non-technical person should be able to fill in an Excel
file and get a readable report, without touching the code.

Explicit requests added over the course of the work, in order:

1. Input and output as **Excel** files, with a **summary chart** in the output.
2. The model must be **user friendly**.
3. The BESS is solved as a **standalone merchant asset on day-ahead arbitrage only**.
4. The price curve must be able to cover **20 real years**, not one year repeated identically
   20 times: price evolution over time is an input, not an implicit assumption of the model.
5. (v3, from comparing against the EDPR model) Prices sheet in **matrix** form: years across
   the top, hours down the side. Battery starting **full**. Output split between a **raw
   data** sheet and a **cleaned data with charts** sheet built with Excel formulas only.
6. (v4, simplification pass) Remove everything that added optionality without adding
   accuracy for a non-programmer running this alone: no separate degradation sheet (folded
   into one mandatory table), no augmentation, no configurable optimization horizon, no
   external price file, no price escalation parameter. Output consolidated from seven sheets
   to four, with full hourly detail (SOC, energy bought, energy sold) now covering all
   20 years instead of just year 1.
7. (v7) No discounting (only each year's revenue), negative prices = charge or idle only
   (kept linear), year 1 fixed at 2029, always 20 years, no charge on injection,
   two tables: capacity degradation (%) and actual RTE per year.

## 2. Starting point: what was wrong with the first version

Before rewriting the model, the original code (`bess_arbitrage_model.py`) was reviewed and a
number of limitations were found, all later fixed or explicitly accepted as a stated
assumption:

| Area | Problem found | How it was resolved |
|---|---|---|
| Degradation | Only energy capacity degraded; RTE and power stayed constant | Added separate degradation tracking for capacity, RTE and power (consolidated into one mandatory table in v4 — see §11; in v7 two tables: capacity degradation and actual RTE, power no longer degrades) |
| Cycling | Rigid **1 cycle/day** constraint, more restrictive than the annual regulatory limit | Replaced with a **365 cycles/year budget**, distributed optimally via a shadow price |
| Continuity | State of charge (SOC) reset to zero every night, energy "lost" between days | SOC now carries over between days; rolling 2-day optimization horizon |
| Timezone | Daily grouping on the raw index: with UTC data the "day" was shifted by 1-2 hours, cutting the evening peak in half | Series always converted to `Europe/Rome` before any calculation |
| Partial years | A year with an incomplete first/last day (e.g. COD mid-day) failed validation | First and last day excluded from the completeness check |
| BESS physics | 0-100% operating SOC, no grid connection limit, no asymmetric power | Added an SOC window (default 5-95%), an optional POI limit, separate charge/discharge power |
| Costs | Charging and discharging happened at the same pure market price, with no grid charges | Added EUR/MWh costs on withdrawal (and, until v6, injection), inside the objective function (they change decisions, not just the result) |
| Perfect foresight | The optimizer knows prices in advance, but this was not compensated for | Added a foresight factor (haircut) on gross revenue |
| Output | Only total revenue, no operating detail | Added MWh charged/discharged, actual cycles, average buy/sell price, captured spread, cycle shadow price |
| Tests | No automated tests | Added unit tests (20 as of v7) covering known analytic solutions, discharged/charged ratio = RTE, cycle budget compliance, etc. |

## 3. What the model computes, in short

For every day of the time horizon, a **linear program** decides in which hours to charge and
in which to discharge to maximise the margin, respecting simultaneously: maximum
charge/discharge power, energy capacity, the SOC operating window, charge/discharge
efficiency, and the annual cycle budget.

SOC is the energy level inside the battery: it rises when buying, falls when selling, capped
at the installed energy (power x discharge hours), with efficiency applied at every step. It
carries over from one day to the next without resetting.

Each year's result is then adjusted by: the foresight factor (haircut on perfect foresight),
availability (maintenance/outages), and the auxiliary load cost. The output is the revenue
of each single year, in k EUR/MW and k EUR/MWh: **nothing is discounted** (v7).

## 4. Assumptions and parameters used — with the actual defaults

Every parameter can be changed by the user in the `Parameters` sheet of the input file. The
table below gives the **default value** set in the model, i.e. the one used if the user does
not change it.

### Sizing
| Parameter | Default | Note |
|---|---|---|
| Nominal power | 1 MW | Reasoning per installed MW by default, so revenue comes out directly in k EUR/MW |
| Duration (discharge hours) | 8 h | Installed energy = Power x Duration. This is the SOC ceiling |
| Max charge / discharge power | = nominal power | Can be set asymmetrically |
| Power at the grid connection point (POI) | no limit | If tighter than the inverter's power |

### Operating window
| Parameter | Default | Note |
|---|---|---|
| Minimum SOC | 5% | No BESS is operated down to 0% |
| Maximum SOC | 95% | |
| Starting SOC | **95% (full)** | The battery is delivered charged. Only applies to the first day; after that SOC is continuous. Still clamped to the maximum SOC |

### Cycling
| Parameter | Default | Note |
|---|---|---|
| Max equivalent cycles/year | 365 (**fixed**) | Annual budget, not a daily one. Constant `MAX_CYCLES_PER_YEAR` in `bess_model.py`, not an input (v6) |
| Max cycles/day | 2 (**fixed**) | Technical safety cap. Constant `MAX_CYCLES_PER_DAY`, not an input (v6) |

### Capacity degradation and RTE (two mandatory tables, v7)
| Table | Default | Note |
|---|---|---|
| CAPACITY DEGRADATION | 100% every year | One row per operating year: energy capacity as a % of nameplate, from 100% down (the supplier's guaranteed curve). It reduces the energy, i.e. the SOC ceiling, of that year |
| RTE | 87% every year | One row per operating year: the asset's **actual** system round-trip efficiency (AC-AC, not cell-level) in that year, e.g. 87% and down. It is NOT a % of a year-1 value: there is no separate "RTE year 1" input, year 1 is the first row |

Power does not degrade: every year it is the nameplate (or POI-limited) value. These two
tables are the only degradation mechanism: no automatic %/year decay formula and no
augmentation (battery replacement is out of scope — see §11). Degradation is exogenous: it
does not depend on how many cycles the model actually does.

### Losses and availability
| Parameter | Default | Note |
|---|---|---|
| Auxiliary consumption (HVAC, etc.) | 0.5% of power | Continuous load, paid at the average market price |
| Availability | 98% | Planned maintenance + outages |

### Variable market costs
| Parameter | Default | Note |
|---|---|---|
| Charges on withdrawal (charging) | 0 EUR/MWh | Transmission, dispatching, etc., if applicable |

There is no charge on injection (discharging): removed in v7.

### Operational realism
| Parameter | Default | Note |
|---|---|---|
| Foresight factor | 85% | Haircut on perfect foresight (typically 80-90%) |
| Optimization horizon | 2 days, **fixed, not a parameter** | See §7 for why it exists and why it isn't user-configurable |

### Time frame (fixed, not parameters — v7)
| Item | Value | Note |
|---|---|---|
| Year 1 | **2029** | Constant `START_YEAR` in `bess_model.py`. The `Year 1` column of the Prices sheet is always 2029 |
| Operating years | **20** (2029-2048) | Constant `LIFETIME_YEARS`. There is no useful-life parameter |
| Discounting | **none** | The output is each year's revenue; no discount rate, no NPV |

Price escalation is **not a parameter**: if growth in the price curve should be modelled, the
user fills in more year-columns with their own numbers (see §5). Likewise, there is no
external CSV price file option: the Prices sheet inside the workbook is the only input path.

### Negative prices (v7)
In every hour with a negative price the battery can **only charge or stay idle**: the upper
bound of discharge in that hour is zero. It is a bound on a variable, so the problem stays a
linear program. Without it, a full battery could sell at a negative price just to make room
for buying at an even more negative one, or charge and discharge in the same hour to burn
energy and be paid for it. With it, simultaneous charge and discharge can no longer pay off
in any hour (at non-negative prices it only loses energy).

## 5. The Prices sheet: matrix format and the two modes

The `Prices` sheet has a fixed structure: a `day` column (1-365), an `hour` column (1-24) and
one column per operating year (`Year 1` = 2029 ... `Year 20` = 2048), for 8,760 rows.
`Year 1` is mandatory; the others can be filled in or left empty.

The prices to enter are the **zonal** day-ahead market prices of the zone the project is in
(NORD, CNOR, CSUD, SUD, SICI, SARD). Using the PUN or the wrong zone is the most costly
possible mistake in this sheet: a BESS's revenue depends on the local intraday spread.

The calendar is a **typical** one: 365 days of exactly 24 hours, no daylight saving time and
no leap years. Internally the curve is handled on a fixed solar-time offset (UTC+1)
specifically for this reason — converting it to the `Europe/Rome` timezone would produce one
23-hour day and one 25-hour day, which make no sense in a typical profile.

**Empty years repeat the last filled one (v7).** The model always runs 20 years. Every
filled column is used as-is for its year. Every empty column takes the prices of the last
filled year before it: with only `Year 1` filled, 2029 is repeated for all 20 years; with
`Year 1`-`Year 5` filled, 2034-2048 repeat `Year 5`. Columns are placed by the number in
their header, so an empty column in the middle never shifts the years after it. Repeating a
year is an approximation (it assumes the intraday spread stops evolving from that year on);
no escalation is ever applied automatically.

## 6. How the 365 cycles/year constraint works

It is not imposed day by day (that would be too restrictive: it would prevent 2 cycles on a
very volatile day being offset by 0 on a flat one). It is imposed as an **annual budget**
through a **cycle shadow price** — an implicit cost, in EUR/MWh of throughput, found by
bisection — representing how much a cycle's wear is "worth": the BESS only cycles when the
day's spread pays for that cost. It is the same principle by which, in reality, an operator
decides whether it's worth spending warranty cycles on a mediocre day.

## 7. Why the optimization horizon is fixed at 2 days, not a parameter

Every day the model also looks at the next day before deciding, but only commits the current
day's decision (a rolling horizon). This exists purely so the model does not make a bad call
right at midnight — for example draining the battery at 23:00 when a much better buying
window opens at 3am the next day, which a model that only ever saw "today" would never see
coming.

This was originally a user-configurable parameter. It has been fixed at 2 days and removed
from the input file because there is no scenario in this model where a non-technical user
should be choosing a different value: 1 day reintroduces the midnight-reset problem this
mechanism exists to solve, and going beyond 2 days changes the result very little while
slowing the calculation down. Removing it from the input sheet also removes one more knob
that could be turned by accident.

## 8. What the output "k EUR/MW" means

The revenue per MW shown in the output is **always divided by the nameplate power** (the one
written in `Parameters`), never by the current year's degraded power. This is intentional:
dividing by a fixed denominator is what makes the decline caused by degradation — capacity,
and RTE together — visible when looking across the years. To get back to that year's
euros:

```
year's EUR = (that year's k EUR/MW) x 1,000 x nameplate power (MW)
```

With power left at 1 MW (the default), revenue in k EUR/MW coincides with revenue in k EUR
and no conversion is needed. Revenue in **k EUR/MWh installed** is also reported alongside
it, which is the right metric for comparing different durations (4h vs 8h).

## 9. Confirmation: capacity and RTE of each year are used in the optimization

Not just declared: the energy capacity (nameplate x that year's capacity %) and the RTE read
from that year's row of the RTE table are the exact values passed to the solver for that
year. So every year the BESS has fewer MWh to shift and a lower efficiency — two effects that
add up, both visible year by year in the `Annual_Results` sheet. Power stays at nameplate.

## 10. Out of scope (by explicit choice, not oversight)

- **MSD / ancillary services**: out of scope. A v5 attempt (MSD as an independent second
  optimization with its own prices, SOC reserve and success rate) was dropped in v6: MSD and
  day-ahead dispatch compete for the same battery, so modelling them as independent
  overstates the revenue, while coupling them is overkill for this model. See §11-bis.
- **Any other revenue stream** (capacity mechanisms included): not modelled.
- **Battery augmentation** (adding cells to restore capacity): removed in v4, by explicit
  request — the batteries in this project will never be swapped or added to, so modelling a
  capacity reset was pure overkill. If a project ever needs it, it would have to be
  reintroduced as its own mechanism; the capacity degradation table has no concept
  of a reset built in (a table row can still be set higher than the previous year's, but the
  model does not derive that from an "augmentation year" the way v3 did).
- **CAPEX, OPEX, taxes, imbalance costs**: the result is a **gross market revenue from
  day-ahead arbitrage only**, to be added downstream in the business plan.

## 11. What changed in v4 (simplification pass)

After the v3 rewrite (which aligned the model with EDPR's own tool — matrix price format,
full starting SOC, raw/cleaned output split), a further, more aggressive simplification pass
was requested, on one explicit principle: **this has to stay usable by someone who does not
program and cannot audit every line — no overkill.** Every change below either removes a
knob that added optionality without adding real accuracy for this use case, or removes output
that was redundant with something else.

**Degradation: three rates + optional curve + augmentation, collapsed into one mandatory
table.** v3 had three independent %/year decay rates (capacity, RTE, power), an optional
`Degrado` sheet that would override them if filled in, and a separate `augmentation_years`
list that reset capacity back to 100% in chosen years. All of that is gone. There is now
exactly one mechanism: a table in the `Parameters` sheet, one row per year, three columns
(capacity/RTE/power as % of nameplate), and it is **mandatory** — if the battery does not
degrade, or the curve isn't known yet, the same value is simply repeated for all 20 rows.
Fewer ways to express the same idea means fewer ways to get it subtly wrong, and it matches
how a supplier's guaranteed degradation curve is actually handed over (a table, not a decay
rate). Augmentation was dropped outright rather than folded in, because the batteries in this
project will never be swapped — carrying a feature for an event that will not happen is the
definition of overkill.

**Optimization horizon: parameter to fixed assumption.** See §7. It moved from the
`Parameters` sheet into this document, because it was never really a decision the user should
be making — it exists to fix a modelling artifact (the midnight reset), not to represent a
real technical choice about the battery.

**External CSV price file: removed.** v3 could ignore the `Prices` sheet entirely and read a
long-format CSV instead. That whole code path is gone: the workbook's `Prices` sheet is now
the only input, which also means there is exactly one price format to explain, one calendar
convention (the typical calendar), and one set of validation error messages.

**Price escalation: removed.** v3 could apply a %/year growth rate to a single repeated price
column. This asked the model to make an assumption about future price growth that is really
the user's to make, so it has been removed: growth is now expressed the only way it can be
trusted, i.e. by filling in more year-columns with the user's own numbers.

**Output consolidated from seven sheets to four, and the raw hourly trace extended to every
year.** `Risultati_Annuali` and `Dati_Puliti` are merged into a single `Annual_Results` sheet.
In v3, `Dati_Puliti` existed specifically so that changing the foresight factor or
availability in `Dati_Grezzi` would recalculate the whole sheet through live Excel formulas,
without re-running python. That live-recalculation feature has been dropped in the merge: v4
writes one table of plain computed values instead of keeping two sheets in sync through
formulas, because a second sheet whose only purpose is to shadow the first with formulas is
exactly the kind of complexity this pass was meant to remove — if a parameter changes, the
model is re-run. The old `Dettaglio_Anno1` sheet (day-by-day margin, year 1 only) is gone too,
replaced by something more useful: `Raw_Data` now carries the model's direct hour-by-hour
output — SOC, energy bought, energy sold — for **every year**, not just the first (up to
175,200 rows for 20 years). The `Ipotesi` sheet (a dump of the input parameters as read) is
gone from the output workbook entirely; this document is now where that information lives,
in full, alongside the reasoning behind each default.

**Charts: thinner lines, more headroom, one more chart.** Value lines across all Summary
charts were made thin (about 0.75pt) and marker-free so 20 yearly categories don't turn into
a solid block that covers titles and axis labels. The degradation chart's y-axis now runs to
102% instead of exactly 100%, so a flat "no degradation" line (the default) doesn't sit
invisibly on the plot's top border. A fourth chart (annual bought/sold in k EUR) was added to
the Summary sheet, since that was previously only visible in the now-removed `Dati_Puliti`
sheet. (In the current version the lines are 2.25pt, so the pastel colours stay visible, and
the "Capacity and RTE" chart runs from 50% to 100%.)

**Full English throughout the code and the Excel file.** Every docstring, comment,
warning/error message, sheet name, parameter label and chart title is in English. The one
exception, by explicit request, is `Manuale_utente.md`, which stays in Italian since it's the
operational guide for a non-technical Italian-speaking user; this document and `README.md`
are in English like everything else.

## 11-bis. What changed in v6

Two further cuts, on the same "no overkill" principle as v4 (v6 is the v4 model; the v5 MSD
work is not part of it).

**Cycle limits fixed, no longer inputs.** 365 equivalent cycles/year and 2 cycles/day are now the constants `MAX_CYCLES_PER_YEAR` and
`MAX_CYCLES_PER_DAY` at the top of `bess_model.py`. They were never something to tune per
scenario, and two more input rows were two more ways to get a result wrong. The mechanism is
unchanged (§6). If a supplier warranty ever allows fewer cycles, the constant is edited in
the code, deliberately, rather than being one more cell in the input sheet.

**MSD removed entirely.** The v5 design ran MSD as a second linear program independent of the
day-ahead one. That independence is exactly what does not hold in reality: both use the same
battery, so their dispatches, SOC and cycle budget compete. Stacking them as if independent
double counts revenue; coupling them properly means one joint optimization, which is more
machinery than this model is meant to have. MSD is left to the business plan.

**Input cells in a softer yellow** (cosmetic).

## 11-ter. What changed in v7

Five changes, all on the same "no overkill" line: the only target is each year's revenue in
k EUR/MW, with sound assumptions.

**No discounting.** The discount rate parameter, the discount factor, the discounted revenue
column and the NPV are gone. The output is the revenue of each single year; the business
plan does its own discounting.

**Negative prices: charge or idle only.** See §4, "Negative prices". This also makes the
two-step correction against simultaneous charge/discharge planned earlier unnecessary.

**Year 1 is always 2029.** The `Calendar year of year 1 (COD)` parameter is gone (constant
`START_YEAR`).

**Always 20 years, empty years repeat the last filled one.** The `Useful life` parameter is
gone (constant `LIFETIME_YEARS`) and the old "multi-year / single year repeated" modes are
replaced by one rule (§5).

**No charge on injection.** Removed from the inputs and from the objective function; the
charge on withdrawal stays.

**Two yearly tables: capacity degradation and RTE.** The old table (capacity, RTE and power,
all as % of nameplate) is replaced by two tables: CAPACITY DEGRADATION (% of nameplate energy,
100% and down) and RTE (the asset's actual RTE each year, e.g. 87% and down). The RTE table
replaces the "RTE year 1" input, so a supplier's RTE curve can be pasted as-is. Power no
longer degrades.

## 12. Project files

| File | Content |
|---|---|
| `bess_model.py` | Engine: BESS spec, capacity and RTE tables, matrix-to-series conversion, LP optimization, annual and lifetime simulation |
| `excel_io.py` | Input template creation, parameter and price-matrix reading, output report writing with charts |
| `run_bess.py` | Command-line script (`--template` to generate the input, then a normal run) |
| `test_bess_model.py` | 20 correctness tests for the engine and the Excel I/O |
| `README.md` | Quick-start guide (English) |
| `docs/Manuale_utente.md` | Full operational manual, including a code walkthrough (Italian) |
| `docs/Riepilogo_modello_BESS.md` | This document |
| `input_bess.xlsx` | Not in the repository: created with `python run_bess.py --template input_bess.xlsx` (demo prices) |
| `risultati_bess.xlsx` | Not in the repository: written by every run of the model |
