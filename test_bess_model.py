"""Correctness tests for the engine (v7). Run with: python test_bess_model.py"""

import os
import tempfile

import numpy as np
import pandas as pd

from bess_model import (LIFETIME_YEARS, MAX_CYCLES_PER_YEAR, START_YEAR, BessSpec, fill_missing_years,
                        is_typical_calendar, make_flat_capacity_table, make_flat_rte_table, make_synthetic_matrix,
                        make_synthetic_prices, matrix_to_series, optimize_window,
                        prepare_price_series, simulate_year)

OK, FAIL = "  OK  ", " FAIL "


def check(name, cond, extra=""):
    print(f"[{OK if cond else FAIL}] {name} {extra}")
    return bool(cond)


def test_analytic_two_prices():
    """A day with 12 hours at 10 EUR/MWh and 12 at 100: the margin has a known solution."""
    prices = np.array([10.0] * 12 + [100.0] * 12)
    e, p = 100.0, 25.0  # 4h, eta=0.9
    r = optimize_window(prices, dt=1.0, e_usable=e, soc_min=0.0, soc_max=e, soc_start=0.0,
                        p_ch=p, p_dis=p, eta_c=0.9, eta_d=0.9, cost_ch=0.0,
                        cycle_price=0.0, max_cycles=1.0)
    # charges 100/0.9 = 111.1 MWh from the grid, discharges 100*0.9 = 90 MWh
    exp_rev = 90 * 100 - (100 / 0.9) * 10
    rev = np.sum(prices * r["discharge_mw"]) - np.sum(prices * r["charge_mw"])
    return check("analytic margin on 2 prices", abs(rev - exp_rev) < 1.0,
                 f"({rev:,.0f} EUR vs expected {exp_rev:,.0f} EUR)")


def test_rte_ratio():
    """Energy discharged over energy charged must exactly equal the RTE."""
    spec = BessSpec(power_mw=50, duration_h=4, rte=0.85, foresight_factor=1.0,
                    availability_pct=1.0, aux_load_pct_of_power=0.0, soc_min_frac=0.0,
                    soc_max_frac=1.0, soc_start_frac=0.0)
    prices = make_synthetic_prices(2027).iloc[: 24 * 30]
    r = simulate_year(prices, spec, validate=False)
    ratio = r["mwh_discharged"] / r["mwh_charged"]
    return check("discharged/charged ratio = RTE", abs(ratio - 0.85) < 0.02,
                 f"({ratio:.4f} vs 0.8500)")


def test_cycle_budget():
    """The fixed annual cycle budget (365) must be respected within tolerance,
    and must actually bind for a short-duration battery on volatile prices."""
    spec = BessSpec(power_mw=50, duration_h=2, rte=0.87,
                    foresight_factor=1.0, availability_pct=1.0)
    prices = make_synthetic_prices(2027)
    r = simulate_year(prices, spec)
    return check("annual cycle budget respected (fixed 365)",
                 r["cycles"] <= MAX_CYCLES_PER_YEAR * 1.02 and r["cycle_shadow_price_eur_mwh"] > 0,
                 f"({r['cycles']:.0f} cycles, shadow price {r['cycle_shadow_price_eur_mwh']:.2f} EUR/MWh)")


def test_capacity_and_rte_tables():
    """v7: capacity follows the CAPACITY DEGRADATION table (% of nameplate), the RTE is
    read AS-IS from the RTE table (actual RTE, not a % of year 1), power never degrades,
    and the last row is held once a table runs out."""
    cap = pd.DataFrame({"year": [1, 5, 10], "capacity_pct": [1.00, 0.94, 0.88]})
    rte = pd.DataFrame({"year": [1, 5, 10], "rte": [0.87, 0.85, 0.83]})
    spec = BessSpec(power_mw=100, duration_h=4, rte=0.50, capacity_table=cap, rte_table=rte)
    f1, f10, f15 = spec.year_factors(1), spec.year_factors(10), spec.year_factors(15)
    ok = (f1["rte"] == 0.87 and f10["rte"] == 0.83            # absolute, not 0.50 x ...
          and f10["energy_mwh"] == 400 * 0.88
          and f10["p_discharge_mw"] == 100 == f1["p_discharge_mw"]
          and f15["capacity_factor"] == 0.88 and f15["rte"] == 0.83)
    return check("capacity table (%) and RTE table (actual RTE)", ok,
                 f"(year 10: capacity {f10['capacity_factor']:.0%}, RTE {f10['rte']:.0%}, "
                 f"power {f10['p_discharge_mw']:.0f} MW)")


def test_no_table_means_no_degradation():
    """Without tables (direct python/API use): capacity 100%, RTE = the constant `rte`."""
    spec = BessSpec(power_mw=100, duration_h=4, rte=0.87)
    f1, f20 = spec.year_factors(1), spec.year_factors(20)
    ok = f1 == f20 and f1["capacity_factor"] == 1.0 and f1["rte"] == 0.87
    return check("no tables -> 100% capacity, constant RTE", ok)


def test_soc_continuity():
    """SOC must carry over from one day to the next (no energy evaporates)."""
    spec = BessSpec(power_mw=50, duration_h=8, rte=0.9,
                    foresight_factor=1.0, availability_pct=1.0)
    prices = make_synthetic_prices(2027).iloc[: 24 * 10]
    r = simulate_year(prices, spec, validate=False)
    d = r["daily_detail"]
    return check("SOC continuity across days", d["soc_end_mwh"].notna().all(),
                 f"(average end-of-day SOC {d['soc_end_mwh'].mean():.1f} MWh)")


def test_partial_year():
    """A partial year with an incomplete first/last day must not raise (fix v1)."""
    prices = make_synthetic_prices(2027).loc["2027-03-15 14:00":"2027-04-20 09:00"]
    spec = BessSpec(power_mw=50, duration_h=4, rte=0.87)
    try:
        r = simulate_year(prices, spec)
        return check("partial year accepted", r["n_days"] > 30, f"({r['n_days']} days)")
    except ValueError as e:
        return check("partial year accepted", False, f"-> {e}")


def test_costs_change_decisions():
    """With high grid charges the BESS must cycle less: costs enter the objective."""
    prices = make_synthetic_prices(2027).iloc[: 24 * 60]
    base = BessSpec(power_mw=50, duration_h=4, rte=0.87, foresight_factor=1.0, availability_pct=1.0)
    costly = BessSpec(power_mw=50, duration_h=4, rte=0.87, foresight_factor=1.0,
                      availability_pct=1.0, charge_cost_eur_mwh=40.0)
    c1 = simulate_year(prices, base, validate=False)["cycles"]
    c2 = simulate_year(prices, costly, validate=False)["cycles"]
    return check("withdrawal charges reduce cycling", c2 < c1, f"({c1:.0f} -> {c2:.0f} cycles)")


def test_no_simultaneous():
    prices = np.array([50.0, -20.0, 120.0, 30.0] * 6)
    r = optimize_window(prices, 1.0, 100.0, 0.0, 100.0, 0.0, 25.0, 25.0, 0.93, 0.93,
                        0.0, 0.0, 1.0)
    both = np.sum((r["charge_mw"] > 1e-6) & (r["discharge_mw"] > 1e-6))
    return check("no simultaneous charge and discharge", both == 0)


def test_negative_prices_charge_only():
    """v7: in hours with a negative price the battery may only charge or stay idle.
    Case built to tempt it: battery FULL at the start, a negative-price block, then a
    high-price block. Without the bound, the LP would sell at -5 to make room and
    buy at -80 (and could even charge and discharge together to burn energy)."""
    prices = np.array([-5.0] * 4 + [-80.0] * 4 + [150.0] * 4 + [60.0] * 12)
    r = optimize_window(prices, 1.0, 100.0, 0.0, 100.0, 100.0, 25.0, 25.0, 0.93, 0.93,
                        0.0, 0.0, 4.0)
    neg = prices < 0
    no_dis = np.all(r["discharge_mw"][neg] == 0.0)
    both = np.sum((r["charge_mw"] > 1e-6) & (r["discharge_mw"] > 1e-6))
    still_sells = r["discharge_mw"][~neg].sum() > 0
    return check("negative price -> charge or idle only", no_dis and both == 0 and still_sells,
                 f"(discharge in negative hours: {r['discharge_mw'][neg].sum():.1f} MW, "
                 f"simultaneous hours: {both})")


def test_timezone():
    """A series in UTC and one in Italian local time must give the same daily grouping."""
    p_local = make_synthetic_prices(2027).iloc[: 24 * 20]
    p_utc = p_local.tz_convert("UTC")
    spec = BessSpec(power_mw=50, duration_h=4, rte=0.87, foresight_factor=1.0, availability_pct=1.0)
    a = simulate_year(p_local, spec, validate=False)["revenue_eur"]
    b = simulate_year(p_utc, spec, validate=False)["revenue_eur"]
    return check("UTC and local time give the same result", abs(a - b) < 1.0,
                 f"({a:,.0f} EUR vs {b:,.0f} EUR)")


# ---------------------------------------------------------------------------
# matrix format, typical calendar, full starting SOC, Excel round-trip
# ---------------------------------------------------------------------------

def test_matrix_to_series():
    """The 8760 x N matrix must become N consecutive 365-day calendar years."""
    m = make_synthetic_matrix(n_years=3)
    s = matrix_to_series(m, start_year=2029)
    years = sorted(s.index.year.unique())
    days = s.groupby(s.index.normalize()).size()
    ok = (m.shape == (8760, 3) and len(s) == 3 * 8760 and years == [2029, 2030, 2031]
          and (days == 24).all())
    return check("matrix -> multi-year series", ok,
                 f"({len(s):,} rows, years {years}, days of {days.unique()} hours)")


def test_typical_calendar_not_shifted():
    """A 'typical calendar' curve must not be converted: hours must stay where they are."""
    m = make_synthetic_matrix(n_years=1)
    s = matrix_to_series(m, start_year=2029)
    p = prepare_price_series(s)
    # the value at row 4000 (day 167, hour 17) must stay at the same hour
    ok = is_typical_calendar(s) and p.index[4000].hour == s.index[4000].hour and p.equals(s)
    return check("typical calendar not re-converted", ok,
                 f"(hour {s.index[4000].hour} -> {p.index[4000].hour})")


def test_soc_starts_full():
    """Default starting SOC must be a full battery (= maximum SOC)."""
    spec = BessSpec(power_mw=1, duration_h=8, rte=0.87)
    e = spec.energy_mwh
    prices = matrix_to_series(make_synthetic_matrix(n_years=1), 2029).iloc[: 24 * 5]
    r = simulate_year(prices, spec, validate=False)
    d = r["daily_detail"]
    # with a full battery, day 1 sells before having bought anything
    first = d.iloc[0]
    ok = (spec.soc_start_frac == 0.95
          and first["mwh_discharged"] > first["mwh_charged"] * spec.rte)
    return check("starting SOC = full battery", ok,
                 f"(day 1: {first['mwh_charged']:.1f} MWh charged, "
                 f"{first['mwh_discharged']:.1f} discharged out of {e:.0f} MWh)")


def test_excel_roundtrip():
    """Template -> read: parameters, prices and the degradation table must come back
    identical, year 1 is 2029 and the series always covers 20 years."""
    from excel_io import create_input_template, read_input

    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "t.xlsx")
        create_input_template(f, n_years=LIFETIME_YEARS)
        spec, prices, sc = read_input(f)
        expected = matrix_to_series(make_synthetic_matrix(n_years=LIFETIME_YEARS).round(2))
        years = sorted(prices.index.year.unique())
        ok = (spec.duration_h == 8.0 and spec.soc_start_frac == 0.95
              and spec.capacity_table["capacity_pct"].eq(1.0).all()
              and spec.rte_table["rte"].eq(0.87).all()
              and len(spec.capacity_table) == len(make_flat_capacity_table(20)) == 20
              and len(spec.rte_table) == len(make_flat_rte_table(20)) == 20
              and years[0] == START_YEAR == 2029 and len(years) == 20
              and sc["price_source"] == list(range(1, 21))
              and np.allclose(prices.to_numpy(), expected.to_numpy()))
        return check("Excel round-trip (template -> read), 2029-2048", ok,
                     f"({len(prices):,} prices, years {years[0]}-{years[-1]})")


def test_excel_rte_table_is_actual_rte():
    """v7: an RTE typed in the Excel RTE table (e.g. 85% in year 10) is the RTE the
    engine uses that year, and the capacity table reduces the energy."""
    import openpyxl

    from excel_io import RTE_HEADER, CAP_HEADER, create_input_template, read_input

    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "t.xlsx")
        create_input_template(f, n_years=1)
        wb = openpyxl.load_workbook(f)
        ws = wb["Parameters"]
        for r in range(1, ws.max_row + 1):
            if ws.cell(r, 2).value == "Year" and ws.cell(r, 3).value == RTE_HEADER:
                ws.cell(r + 10, 3).value = 0.85          # year 10
            if ws.cell(r, 2).value == "Year" and ws.cell(r, 3).value == CAP_HEADER:
                ws.cell(r + 10, 3).value = 0.80          # year 10
        wb.save(f)
        spec, _, _ = read_input(f)
        f10, f9 = spec.year_factors(10), spec.year_factors(9)
        ok = (abs(f10["rte"] - 0.85) < 1e-12 and abs(f9["rte"] - 0.87) < 1e-12
              and abs(f10["energy_mwh"] - 0.80 * spec.energy_mwh) < 1e-12)
        return check("Excel RTE table = actual RTE, capacity table = % of energy", ok,
                     f"(year 10: RTE {f10['rte']:.1%}, energy {f10['energy_mwh']:.2f} MWh)")


def test_empty_years_repeat_last_filled():
    """v7: empty year-columns take the prices of the last filled year before them,
    and an empty column never shifts the years after it."""
    import openpyxl

    from excel_io import create_input_template, read_input

    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "t.xlsx")
        create_input_template(f, n_years=5)          # Year 1-5 filled, 6-20 empty
        wb = openpyxl.load_workbook(f)
        ws = wb["Prices"]
        for r in range(2, 8762):                      # also empty Year 3
            ws.cell(r, 5).value = None
        wb.save(f)
        _, prices, sc = read_input(f)
        m = prices.to_numpy().reshape(20, 8760)
        src = sc["price_source"]
        ok = (src == [1, 2, 2, 4] + [5] * 16
              and np.array_equal(m[2], m[1])          # 2031 = Year 2
              and np.array_equal(m[19], m[4])         # 2048 = Year 5
              and not np.array_equal(m[3], m[1])
              and len(prices) == 20 * 8760)
        return check("empty years repeat the last filled one", ok, f"(sources {src})")


def test_single_column_repeated_20_years():
    """A single filled column is repeated for all 20 years; Year 1 is mandatory."""
    one = make_synthetic_matrix(n_years=1)[:, 0]
    m, src = fill_missing_years([one])
    try:
        fill_missing_years([None, one])
        year1_mandatory = False
    except ValueError:
        year1_mandatory = True
    ok = m.shape == (8760, 20) and src == [1] * 20 and year1_mandatory
    return check("single column -> 20 identical years, Year 1 mandatory", ok, f"({m.shape})")


def test_revenue_identity():
    """
    Net revenue must match the formula the Summary sheet is built from:
    sales*f*a - purchases*f*a - auxiliary load.
    """
    spec = BessSpec(power_mw=1, duration_h=8, rte=0.87, foresight_factor=0.85,
                    availability_pct=0.98)
    prices = matrix_to_series(make_synthetic_matrix(n_years=1), 2029).iloc[: 24 * 30]
    r = simulate_year(prices, spec, validate=False)
    adj = spec.foresight_factor * spec.availability_pct
    formula = r["sell_revenue_raw_eur"] * adj - r["buy_cost_raw_eur"] * adj - r["aux_cost_eur"]
    return check("Excel formula = engine revenue", abs(formula - r["revenue_eur"]) < 1e-6,
                 f"({formula:,.2f} EUR vs {r['revenue_eur']:,.2f} EUR)")


def test_hourly_output_covers_every_year():
    """summary['hourly_all'] must carry SOC/bought/sold for every hour of every year,
    and no discounting may appear anywhere in the annual table."""
    from bess_model import simulate_lifetime

    m = make_synthetic_matrix(n_years=2)
    prices = matrix_to_series(m, start_year=2029)
    spec = BessSpec(power_mw=1, duration_h=4, rte=0.87)
    df, summary = simulate_lifetime(prices, spec, progress=False)
    hourly = summary["hourly_all"]
    expected_cols = {"year_index", "calendar_year", "day", "hour", "soc_mwh", "bought_mwh", "sold_mwh"}
    ok = (len(hourly) == 2 * 8760 and expected_cols.issubset(hourly.columns)
          and hourly["soc_mwh"].notna().all()
          and not any("discount" in c for c in df.columns))
    return check("hourly output covers every year, nothing discounted", ok,
                 f"({len(hourly):,} rows)")


if __name__ == "__main__":
    print("BESS engine tests\n" + "-" * 68)
    tests = [test_analytic_two_prices, test_rte_ratio, test_cycle_budget, test_capacity_and_rte_tables,
             test_no_table_means_no_degradation, test_soc_continuity, test_partial_year,
             test_costs_change_decisions, test_no_simultaneous, test_negative_prices_charge_only,
             test_timezone, test_matrix_to_series, test_typical_calendar_not_shifted,
             test_soc_starts_full, test_excel_roundtrip, test_excel_rte_table_is_actual_rte,
             test_empty_years_repeat_last_filled,
             test_single_column_repeated_20_years, test_revenue_identity,
             test_hourly_output_covers_every_year]
    results = [t() for t in tests]
    print("-" * 68)
    print(f"{sum(results)}/{len(results)} tests passed")
