# BESS revenue model — day-ahead arbitrage (v7)

Python model that estimates the revenue of a battery energy storage system (BESS) from
day-ahead market arbitrage, year by year over 20 operating years (2029-2048), with a
user-supplied capacity degradation curve and RTE curve. Inputs and outputs are Excel files,
so the model can be used without touching the code.

It is the merchant-revenue engine of the M.Sc. thesis *"BESS Remuneration Model — Building a
revenue and financial model for a standalone BESS, with a case study on Italy's MACSE
mechanism"* (Flavio Iacuitto, Politecnico di Milano, M.Sc. in Energy Engineering, A.Y.
2025/2026, supervisor Prof. Matteo Vincenzo Rocco). Its yearly output feeds the "Arbitrage"
row of the Excel business plan described in the thesis.

The result is each year's revenue in k EUR/MW (and k EUR/MWh): nothing is discounted.

## Repository content

| File | Content |
|---|---|
| `bess_model.py` | Engine: BESS specification, price handling, daily linear program, annual and lifetime simulation |
| `excel_io.py` | Excel input template, input reading, output report with charts |
| `run_bess.py` | Command-line launcher |
| `test_bess_model.py` | 20 correctness tests |
| `requirements.txt` | Python dependencies |
| `docs/Manuale_utente.md` | Full user guide, including a block-by-block walkthrough of the code (Italian) |
| `docs/Riepilogo_modello_BESS.md` | Requirements, assumptions, defaults and version history (English) |

## Installation

Python 3.10 or later.

```bash
git clone https://github.com/<your-username>/bess-revenue-model.git
cd bess-revenue-model
pip install -r requirements.txt
python test_bess_model.py        # must print "20/20 tests passed"
```

## Usage

```bash
# 1. create the input file (already filled with 20 years of demo prices)
python run_bess.py --template input_bess.xlsx

# 2. fill in the 'Parameters' and 'Prices' sheets, then run
python run_bess.py input_bess.xlsx risultati_bess.xlsx
```

Typical run time: about 1 minute.

Template options:

```bash
python run_bess.py --template input.xlsx --years 5      # demo prices only in Year 1-5
python run_bess.py --template input.xlsx --no-prices    # empty Prices sheet, headers only
```

**The template's prices are synthetic demo data**, generated only to show that the model
runs. Replace them with the zonal day-ahead prices of your own market zone before reading any
result. The repository contains no real price, battery or project data.

## Input file (Excel)

| Sheet | Content |
|---|---|
| `Instructions` | How to use it, what it computes and what it doesn't |
| `Parameters` | All technical and economic inputs, plus two mandatory yearly tables (capacity degradation and RTE). **Only the pale-yellow cells should be touched** |
| `Prices` | Hourly zonal prices in **matrix** form: `day` (1-365), `hour` (1-24), and one column per year (`Year 1` = 2029 ... `Year 20` = 2048) |

### Fixed time frame

`Year 1` is always **2029** and the model always runs **20 years** (2029-2048). There is no
COD parameter and no useful-life parameter.

### The Prices sheet

8,760 rows (365 days x 24 hours) and 20 year-columns, filled with the day-ahead prices of
**your own zone** (NORD, CNOR, CSUD, SUD, SICI, SARD), not the PUN.

`Year 1` is mandatory. Any year-column left **empty takes the prices of the last filled year
before it**: one single column is repeated for all 20 years; `Year 1`-`Year 5` filled means
2034-2048 repeat `Year 5`. Columns are placed by the number in their header, so an empty
column never shifts the years after it.

The calendar is a **typical** one: 365 days of exactly 24 hours, no daylight saving time and
no leap years.

### Capacity degradation and RTE (two mandatory tables)

At the bottom of the `Parameters` sheet, one row per operating year:

- **CAPACITY DEGRADATION** — energy capacity as a % of nameplate, starting at 100% and going
  down (the supplier's guaranteed curve).
- **RTE** — the asset's **actual** round-trip efficiency in that year (e.g. 87%, 86%, ...),
  not a percentage of the year-1 value. There is no separate "RTE year 1" input: year 1 is
  the first row.

Power does not degrade. If a value does not change, repeat it for every year.

## Output file (Excel)

| Sheet | Content |
|---|---|
| `Summary` | Main KPIs + 4 charts: annual revenue, capacity and RTE per year, cycles per year, bought/sold per year |
| `Annual_Results` | One row per operating year: capacity (% and MWh), RTE, MWh bought/sold, prices, revenue in k EUR, k EUR/MW, k EUR/MWh |
| `Raw_Data` | The direct hour-by-hour output of the model for all 20 years: SOC, energy bought, energy sold (175,200 rows) |
| `Sample_Day` | Hourly profile of one day with a price/charge/discharge/SOC chart |

## How it works

For each day it solves a linear program that decides how much to charge and discharge in
every hour to maximise the margin, respecting power, capacity, the SOC window, efficiency
and the cycle limits. It looks two days ahead but only commits the first day's decision,
and the state of charge carries over from one day to the next. By default the battery
starts **full**.

**Negative prices:** in every hour with a negative price the battery can **only charge or
stay idle**, never discharge (an upper bound of zero on discharge in that hour, so the
problem stays linear).

The **365 equivalent-cycles/year** limit (fixed, with a 2 cycles/day cap) is enforced as an
annual budget through a cycle shadow price found by bisection.

The optimizer's revenue is then adjusted by the **foresight factor** (haircut on perfect
foresight), **availability**, and the **auxiliary load cost**.

## Scope

The result is a **gross market revenue from day-ahead arbitrage only**, per year, not
discounted. Every other revenue stream, CAPEX, OPEX, taxes and imbalance costs are added
downstream in the business plan.

## Tests

```bash
python test_bess_model.py
```

20 tests, including: analytic margin, discharged/charged ratio = RTE, cycle budget,
capacity and RTE tables (RTE read as the actual RTE), SOC continuity, no simultaneous
charge/discharge, **negative price -> charge or idle only**, Excel round-trip on 2029-2048,
**empty years repeat the last filled one**, identity between the Summary revenue formula and
the engine, hourly output covering every year with nothing discounted.

## How to cite

> F. Iacuitto, *BESS revenue model — day-ahead arbitrage* (thesis version), 2026.
> GitHub repository: https://github.com/<your-username>/bess-revenue-model

## License

Released under the MIT License: see `LICENSE`.
