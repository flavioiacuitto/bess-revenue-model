"""
BESS model launcher - v7.

USAGE
-----
1) Create the input file (already filled with demo prices):
       python run_bess.py --template input_bess.xlsx

2) Fill it in (the 'Parameters' and 'Prices' sheets), then run:
       python run_bess.py input_bess.xlsx risultati_bess.xlsx

If the output name is omitted, it defaults to 'risultati_bess.xlsx'.
Year 1 is always 2029 and the model always runs 20 years (2029-2048).
"""

from __future__ import annotations

import argparse
import sys
import time

from bess_model import LIFETIME_YEARS, START_YEAR, example_day_profile, simulate_lifetime
from excel_io import create_input_template, read_input, write_output


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BESS productivity model (day-ahead market arbitrage).")
    ap.add_argument("input", nargs="?", help="input Excel file")
    ap.add_argument("output", nargs="?", default="risultati_bess.xlsx", help="output Excel file")
    ap.add_argument("--template", metavar="FILE", help="create an input file and exit")
    ap.add_argument("--years", type=int, default=LIFETIME_YEARS,
                    help="how many year-columns of the template get demo prices (default 20, max 20)")
    ap.add_argument("--no-prices", action="store_true",
                    help="create the template with an empty Prices sheet (headers only)")
    args = ap.parse_args(argv)

    if args.template:
        path = create_input_template(args.template, with_demo_prices=not args.no_prices,
                                     n_years=args.years)
        print(f"Template created: {path}")
        print("Fill in the 'Parameters' and 'Prices' sheets, then re-run pointing at the file.")
        return 0

    if not args.input:
        ap.print_help()
        return 1

    t0 = time.time()
    print(f"Reading input: {args.input}")
    spec, prices, sc = read_input(args.input)
    print(f"  BESS: {spec.power_mw:g} MW / {spec.duration_h:g} h "
          f"({spec.energy_mwh:g} MWh), RTE {spec.year_factors(1)['rte']:.1%} (year 1) -> "
          f"{spec.year_factors(LIFETIME_YEARS)['rte']:.1%} (year 20), "
          f"capacity {spec.year_factors(LIFETIME_YEARS)['capacity_factor']:.0%} in year 20")
    src = sc["price_source"]
    n_filled = len(set(src))
    print(f"  Prices: {len(prices):,} observations, {START_YEAR}-{START_YEAR + LIFETIME_YEARS - 1}, "
          f"average {prices.mean():.1f} EUR/MWh, {int((prices < 0).sum()):,} negative hours")
    if n_filled < LIFETIME_YEARS:
        repeated = [f"{START_YEAR + i}<-Year {s}" for i, s in enumerate(src) if s != i + 1]
        print(f"  {n_filled} year-column(s) filled; empty years repeat the last filled one: "
              + ", ".join(repeated))
    print("Running simulation...")

    annual, summary = simulate_lifetime(prices, spec, price_source=src)

    day = None
    if sc.get("example_day"):
        try:
            year_idx = int(sc["example_day"][:4]) - START_YEAR + 1
            day = example_day_profile(
                prices, spec, sc["example_day"], year_index=year_idx,
                cycle_price=float(annual["cycle_shadow_price_eur_mwh"].iloc[year_idx - 1]))
        except (KeyError, IndexError, ValueError):
            print(f"  (sample day '{sc['example_day']}' not found in the price series: chart skipped)")

    write_output(args.output, annual, summary, spec, sc, day)

    print(f"\nYear 1 (2029) revenue:  {annual['revenue_k_eur_per_mw'].iloc[0]:>8,.1f} k EUR/MW "
          f"({annual['revenue_k_eur_per_mwh'].iloc[0]:,.1f} k EUR/MWh)")
    print(f"Average annual revenue: {summary['revenue_avg_k_eur_per_mw']:>8,.1f} k EUR/MW")
    print(f"\nOutput written to: {args.output}   ({time.time() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
