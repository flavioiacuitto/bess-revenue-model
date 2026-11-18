"""
BESS Arbitrage & Revenue Model - v7
====================================

Dispatch optimization engine (charge/discharge) for a BESS on the day-ahead
market, over 20 operating years (2029-2048).

v7 changes, on top of v6:
  1. No discounting: the output is the revenue of each year, nothing is
     discounted (no discount rate, no NPV).
  2. Negative prices: in every hour with a negative price the battery can
     only charge or stay idle. This is a per-hour upper bound of zero on
     discharge, so the problem stays linear. It also removes the only case
     in which simultaneous charge and discharge could pay off.
  3. Year 1 is always 2029 (constant START_YEAR, not a parameter).
  4. Always 20 years (constant LIFETIME_YEARS): no 'useful life' parameter.
     Price years left empty repeat the last filled year before them.
  5. No charge on injection (discharging): removed from the inputs and from
     the objective function. The charge on withdrawal stays.
  6. Two yearly tables instead of the old three-column degradation table:
     - CAPACITY DEGRADATION: energy capacity as % of nameplate (100% and down);
     - RTE: the asset's ACTUAL round-trip efficiency each year (e.g. 87% and
       down), not a degradation factor. It replaces the 'RTE year 1' input.
     Power no longer degrades (it is always the nameplate/POI value).

v6 had made the cycle limits fixed constants (MAX_CYCLES_PER_YEAR /
MAX_CYCLES_PER_DAY, below) instead of user parameters and dropped the v5
experiment with MSD.

v4 simplified the model on purpose, after user feedback that v3 had grown too
complex for someone who does not write code and cannot audit every parameter:

  A. Degradation became a single MANDATORY table instead of an optional
     curve plus three %/year rates (in v7 split into the capacity table and
     the RTE table, see point 6 above).
  B. Augmentation (cell replacement mid-life) removed entirely: it added a
     parameter and a code path for something the user's batteries will
     never do.
  C. The optimization horizon is fixed at 2 days (HORIZON_DAYS below) and is
     no longer a user parameter. It only ever existed to avoid a bad
     decision exactly at the midnight boundary; there was never a reason to
     let a non-technical user change it.
  D. Price escalation removed. If prices should grow over time, the user
     writes that growth directly into the price matrix (more columns), not
     through an automatic %/year multiplier hidden in the code.
  E. External price file (CSV) removed. Prices always live in the workbook's
     Prices sheet, in matrix form. One input path, not two.
  F. The engine now keeps the hour-by-hour SOC, energy bought and energy
     sold for every year, not just a same-year daily rollup. This is what
     feeds the Raw_Data sheet in the output workbook.

Everything below is the same LP-based dispatch engine as v3: for each day, a
linear program decides how much to charge and discharge in every hour to
maximise the day's margin, subject to power, capacity, SOC window,
round-trip efficiency and the annual cycle budget. Degradation, the
foresight haircut, availability and auxiliary consumption are still applied
as in v3.

OUT OF SCOPE (by explicit choice): any revenue other than day-ahead
arbitrage, e.g. MSD/ancillary services (a v5 attempt was dropped: MSD cannot
be modelled credibly as independent of the day-ahead dispatch). This model
estimates a pure "merchant" BESS on day-ahead arbitrage only.
"""

from __future__ import annotations

import datetime as dt
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import linprog

_ZERO_TOL = 1e-6
TZ = "Europe/Rome"

# Fixed-offset timezone used for "typical calendar" price curves (365 x 24,
# no daylight saving time): Italian winter time, constant all year round.
TZ_TYPICAL_YEAR = dt.timezone(dt.timedelta(hours=1))

# The optimizer always looks HORIZON_DAYS ahead (today + tomorrow) when
# deciding today's dispatch, but only ever commits today's decision. This is
# what stops the model from making a short-sighted choice right before
# midnight (e.g. draining the battery at 23:00 when a much better buying
# window opens at 03:00 the next day). It is fixed, not a user parameter:
# raising it further changes the result very little and slows the solve.
HORIZON_DAYS = 2

# Cycle limits, fixed (not user parameters): 365 equivalent cycles per year,
# distributed over the year through a shadow price; 2 cycles per day is a
# technical safety cap.
MAX_CYCLES_PER_YEAR = 365.0
MAX_CYCLES_PER_DAY = 2.0

# Time frame, fixed (not user parameters): operating year 1 is always 2029,
# and the model always runs 20 operating years (2029-2048).
START_YEAR = 2029
LIFETIME_YEARS = 20

# Cache of triangular (cumulative-sum) matrices, keyed by window size.
_TRI_CACHE: dict[int, np.ndarray] = {}


def _tri(n: int) -> np.ndarray:
    if n not in _TRI_CACHE:
        _TRI_CACHE[n] = np.tril(np.ones((n, n)))
    return _TRI_CACHE[n]


# ---------------------------------------------------------------------------
# 1. BESS specification
# ---------------------------------------------------------------------------

@dataclass
class BessSpec:
    # --- sizing ---
    power_mw: float                          # nameplate power (MW)
    duration_h: float                        # nameplate duration (h) -> energy = power * duration
    rte: float = 0.85                        # round-trip efficiency used ONLY when no
                                             # rte_table is given (direct python use);
                                             # from Excel the RTE always comes from the table

    power_charge_mw: float | None = None     # None = same as power_mw
    power_discharge_mw: float | None = None  # None = same as power_mw
    poi_limit_mw: float | None = None        # grid connection limit (MW), if tighter

    # --- operating window ---
    soc_min_frac: float = 0.05               # minimum SOC (fraction of usable capacity)
    soc_max_frac: float = 0.95               # maximum SOC
    soc_start_frac: float = 0.95             # starting SOC: battery delivered full
                                             # (still clamped between soc_min and soc_max)

    # --- yearly tables (both mandatory from Excel) ---
    capacity_table: pd.DataFrame | None = None
    # Columns: year, capacity_pct. Energy capacity as a fraction of nameplate
    # (1.00 = 100%), one row per operating year: the degradation curve.
    rte_table: pd.DataFrame | None = None
    # Columns: year, rte. The asset's actual round-trip efficiency in that
    # year (e.g. 0.87, 0.86, ...), NOT a fraction of a year-1 value.
    # Both are left as None only for direct python/API use: then capacity
    # stays at 100% and the RTE is the constant `rte` above.

    # --- losses and availability ---
    aux_load_pct_of_power: float = 0.005     # auxiliary/HVAC load as % of nameplate power (continuous)
    availability_pct: float = 0.98           # availability (planned maintenance + outages)

    # --- variable market costs (grid charges on withdrawal) ---
    charge_cost_eur_mwh: float = 0.0         # extra EUR/MWh paid when charging

    # --- operational realism ---
    foresight_factor: float = 0.85           # haircut on perfect-foresight optimization (typ. 0.80-0.90)

    label: str = ""

    # ------------------------------------------------------------------
    @property
    def energy_mwh(self) -> float:
        return self.power_mw * self.duration_h

    @property
    def p_charge(self) -> float:
        p = self.power_charge_mw if self.power_charge_mw else self.power_mw
        return min(p, self.poi_limit_mw) if self.poi_limit_mw else p

    @property
    def p_discharge(self) -> float:
        p = self.power_discharge_mw if self.power_discharge_mw else self.power_mw
        return min(p, self.poi_limit_mw) if self.poi_limit_mw else p

    # ------------------------------------------------------------------
    @staticmethod
    def _lookup(table: pd.DataFrame | None, col: str, year_index: int, default: float) -> float:
        """Value of `col` for `year_index`; if the table runs out, the last
        row is held constant; if there is no table, `default`."""
        if table is None or len(table) == 0:
            return default
        row = table[table["year"] == year_index]
        if len(row) == 0:
            row = table[table["year"] == table["year"].max()]
        return float(row[col].iloc[0])

    def year_factors(self, year_index: int) -> dict:
        """
        Battery characteristics for operating year `year_index` (1 = 2029):
        - capacity: nameplate energy x the CAPACITY DEGRADATION table value;
        - RTE: read directly from the RTE table (it is the actual RTE);
        - power: always the nameplate (or POI-limited) value.
        """
        cap_f = self._lookup(self.capacity_table, "capacity_pct", year_index, 1.0)
        rte = self._lookup(self.rte_table, "rte", year_index, self.rte)
        return {
            "capacity_factor": cap_f,
            "energy_mwh": self.energy_mwh * cap_f,
            "rte": min(rte, 0.999),
            "p_charge_mw": self.p_charge,
            "p_discharge_mw": self.p_discharge,
        }


# ---------------------------------------------------------------------------
# 2. Price series preparation and validation
# ---------------------------------------------------------------------------

def is_typical_calendar(prices: pd.Series) -> bool:
    """
    True if the series is a "typical calendar" curve: index with a FIXED
    UTC offset different from UTC itself. This is the signature left by
    `matrix_to_series`: 365 days of exactly 24 hours, no daylight saving
    time. UTC is deliberately excluded, because it is a real timezone used
    by real data and should be treated as such (converted to Italian time).
    """
    tz = getattr(prices.index, "tz", None)
    if tz is None or not isinstance(tz, dt.timezone):
        return False
    return tz.utcoffset(None) != dt.timedelta(0)


def prepare_price_series(prices: pd.Series, tz: str = TZ) -> pd.Series:
    """
    Converts the series to Italian local time. A "typical calendar" curve
    (fixed-offset timezone, see `is_typical_calendar`) is left as-is: it is
    already a normalized hourly profile without daylight saving time, and
    converting it would shift the hours around for no reason.
    """
    prices = prices.sort_index()
    if not isinstance(prices.index, pd.DatetimeIndex):
        raise ValueError("The price series must have a datetime index.")
    if is_typical_calendar(prices):
        return prices
    if prices.index.tz is None:
        warnings.warn(
            "Price series has no timezone: assuming Italian local time "
            f"({tz}). If the data is in UTC, localize it first.",
            stacklevel=2,
        )
        prices = prices.tz_localize(tz, ambiguous="infer", nonexistent="shift_forward")
    else:
        prices = prices.tz_convert(tz)
    return prices


def validate_price_series(prices: pd.Series, dt_hours: float | None = None) -> None:
    """Quality checks: no duplicate timestamps, no missing values, no days
    with an abnormal number of observations (allowing for daylight-saving
    23h/25h days on real, non-typical-calendar series)."""
    if prices.index.duplicated().any():
        n = int(prices.index.duplicated().sum())
        raise ValueError(f"Found {n} duplicate timestamps in the price series.")
    if prices.isna().any():
        n = int(prices.isna().sum())
        raise ValueError(f"Found {n} missing (NaN) values in the price series.")

    d = dt_hours if dt_hours is not None else infer_dt_hours(prices)
    expected = 24.0 / d
    counts = prices.groupby(prices.index.normalize()).size()
    if len(counts) > 2:
        counts = counts.iloc[1:-1]  # first/last day may legitimately be partial
    lo, hi = expected * 0.9, expected * 1.1  # tolerance for daylight saving (23h/25h)
    bad = counts[(counts < lo) | (counts > hi)]
    if len(bad) > 0:
        raise ValueError(
            f"Days with an abnormal number of observations (step {d}h, expected ~{expected:.0f}/day). "
            f"Likely gaps in the data. First few: {bad.head(5).to_dict()}"
        )


def infer_dt_hours(prices: pd.Series) -> float:
    """Time step in hours, inferred from the median of the index differences."""
    deltas = prices.sort_index().index.to_series().diff().dropna()
    if len(deltas) == 0:
        return 1.0
    return float(deltas.median().total_seconds() / 3600.0)


# ---------------------------------------------------------------------------
# 2-bis. Price curve in MATRIX form (typical calendar)
# ---------------------------------------------------------------------------

def fill_missing_years(columns: list) -> tuple[np.ndarray, list[int]]:
    """
    Builds the full 20-year price matrix (8,760 x LIFETIME_YEARS) from the
    year-columns of the Prices sheet, some of which may be empty (None).

    Rule (v7): every empty year takes the prices of the last filled year
    before it. Year 1 must be filled. So one single column is repeated for
    all 20 years, and e.g. columns 1-5 filled give years 6-20 = year 5.

    Returns the matrix and, for each operating year, the number of the
    year-column its prices actually come from (1-based).
    """
    cols = list(columns)[:LIFETIME_YEARS]
    cols += [None] * (LIFETIME_YEARS - len(cols))
    if cols[0] is None:
        raise ValueError("'Year 1' (2029) is empty in the Prices sheet: it is mandatory.")
    out, source, last, last_k = [], [], None, None
    for k, c in enumerate(cols):
        if c is not None:
            last, last_k = np.asarray(c, dtype=float), k + 1
        out.append(last)
        source.append(last_k)
    return np.column_stack(out), source


def matrix_to_series(matrix: np.ndarray | pd.DataFrame, start_year: int = START_YEAR,
                     dt_hours: float = 1.0) -> pd.Series:
    """
    Converts a "typical calendar" price matrix into one continuous time
    series.

    matrix: rows = intervals of the year in chronological order (365 days x
            24 hours = 8,760 rows at hourly step), columns = operating
            years (Year 1, Year 2, ... up to 20).

    Each column becomes one consecutive calendar year starting at
    `start_year` (always 2029 in normal use), pasted one after another into
    a single series.

    The calendar is "typical": 24 hours per day, no daylight saving time.
    This is why the index uses a FIXED-offset timezone (winter-time UTC+1)
    rather than Europe/Rome: a typical year has no 23-hour or 25-hour day.
    """
    arr = matrix.to_numpy(dtype=float) if isinstance(matrix, pd.DataFrame) else np.asarray(matrix, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    n_rows, n_years = arr.shape
    if n_years > 20:
        raise ValueError(f"The price matrix has {n_years} year-columns: the maximum is 20.")
    if np.isnan(arr).any():
        bad = int(np.isnan(arr).sum())
        raise ValueError(f"The price matrix has {bad} empty cells: fill them in or remove the column.")

    per_day = int(round(24.0 / dt_hours))
    if n_rows % per_day != 0:
        warnings.warn(
            f"The matrix has {n_rows} rows, not a multiple of {per_day} (one full day): "
            "the last day will be treated as partial.", stacklevel=2)

    freq = f"{int(round(dt_hours * 60))}min"
    chunks = []
    for k in range(n_years):
        idx = pd.date_range(f"{start_year + k}-01-01", periods=n_rows, freq=freq, tz=TZ_TYPICAL_YEAR)
        chunks.append(pd.Series(arr[:, k], index=idx))
    return pd.concat(chunks).rename("price_eur_mwh")


# ---------------------------------------------------------------------------
# 3. Optimization over one time window
# ---------------------------------------------------------------------------

def optimize_window(prices: np.ndarray, dt: float, e_usable: float, soc_min: float,
                    soc_max: float, soc_start: float, p_ch: float, p_dis: float,
                    eta_c: float, eta_d: float, cost_ch: float,
                    cycle_price: float, max_cycles: float | None) -> dict:
    """
    Linear program over a window of N intervals.

    Variables: c_t (charge, grid-side MW), d_t (discharge, grid-side MW), >= 0.
    Objective: maximise  sum[p_t*d_t*dt] - sum[(p_t + cost_ch)*c_t*dt]
                         - cycle_price * throughput
    where `cycle_price` (EUR/MWh of throughput) is the cycle shadow price
    that enforces the ANNUAL cycle budget.

    Negative prices (v7): in every interval with p_t < 0 the upper bound of
    d_t is 0, so the battery can only charge or stay idle. It is a bound on
    a variable, so the problem stays linear.
    """
    n = len(prices)
    tri = _tri(n)

    # battery-side energy per MW-interval
    k_c = eta_c * dt      # MWh stored per MW charged
    k_d = dt / eta_d      # MWh drawn from the battery per MW discharged

    # objective function (linprog minimizes)
    obj_c = (prices + cost_ch) * dt + cycle_price * 0.5 * k_c
    obj_d = -prices * dt + cycle_price * 0.5 * k_d
    c_obj = np.concatenate([obj_c, obj_d])

    # negative price -> no discharge in that interval (charge or idle only)
    bounds = [(0.0, p_ch)] * n + [(0.0, 0.0 if p < 0 else p_dis) for p in prices]

    # SOC_t = soc_start + cumsum(k_c*c - k_d*d)
    A_soc = np.hstack([k_c * tri, -k_d * tri])
    rows = [A_soc, -A_soc]
    rhs = [np.full(n, soc_max - soc_start), np.full(n, soc_start - soc_min)]

    if max_cycles is not None:
        A_cy = np.concatenate([0.5 * k_c * np.ones(n), 0.5 * k_d * np.ones(n)]).reshape(1, -1)
        rows.append(A_cy)
        rhs.append(np.array([max_cycles * e_usable]))

    res = linprog(c_obj, A_ub=np.vstack(rows), b_ub=np.concatenate(rhs),
                  bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"Optimization failed: {res.message}")

    charge = np.where(np.abs(res.x[:n]) < _ZERO_TOL, 0.0, res.x[:n])
    discharge = np.where(np.abs(res.x[n:]) < _ZERO_TOL, 0.0, res.x[n:])

    # Sanity check: simultaneous charge and discharge should never happen
    # (at non-negative prices it only loses energy, and at negative prices
    # discharge is bounded to zero above).
    simult = np.sum((charge > _ZERO_TOL) & (discharge > _ZERO_TOL))
    if simult > 0:
        warnings.warn(f"{simult} intervals with simultaneous charge and discharge (degenerate solution).",
                      stacklevel=2)

    soc = soc_start + np.cumsum(k_c * charge - k_d * discharge)
    return {"charge_mw": charge, "discharge_mw": discharge, "soc_mwh": soc}


# ---------------------------------------------------------------------------
# 4. Simulating one year (rolling horizon + annual cycle budget)
# ---------------------------------------------------------------------------

def _run_year_at_lambda(days: list[tuple], spec: BessSpec, f: dict, dt_h: float,
                        cycle_price: float) -> tuple[pd.DataFrame, dict]:
    """
    Runs the full year at a given cycle shadow price. Returns the
    day-by-day summary (for the cycle-budget search) and the hour-by-hour
    SOC / energy-bought / energy-sold trace for the whole year.
    """
    e_usable = f["energy_mwh"]
    soc_min = spec.soc_min_frac * e_usable
    soc_max = spec.soc_max_frac * e_usable
    soc = min(max(spec.soc_start_frac * e_usable, soc_min), soc_max)

    eta = np.sqrt(f["rte"])
    n_total = sum(len(arr) for _, arr in days)
    soc_hourly = np.empty(n_total)
    charged_hourly = np.empty(n_total)
    discharged_hourly = np.empty(n_total)
    pos = 0
    out = []

    for i, (day, arr) in enumerate(days):
        # window = today + lookahead; only today's decision is committed
        window = np.concatenate([days[j][1] for j in range(i, min(i + HORIZON_DAYS, len(days)))])
        n_today = len(arr)

        r = optimize_window(
            prices=window, dt=dt_h, e_usable=e_usable, soc_min=soc_min, soc_max=soc_max,
            soc_start=soc, p_ch=f["p_charge_mw"], p_dis=f["p_discharge_mw"],
            eta_c=eta, eta_d=eta, cost_ch=spec.charge_cost_eur_mwh,
            cycle_price=cycle_price, max_cycles=MAX_CYCLES_PER_DAY * HORIZON_DAYS,
        )

        ch = r["charge_mw"][:n_today]
        di = r["discharge_mw"][:n_today]
        soc_path = r["soc_mwh"][:n_today]
        soc = float(soc_path[-1])       # SOC continuity into the next day

        soc_hourly[pos:pos + n_today] = soc_path
        charged_hourly[pos:pos + n_today] = ch * dt_h
        discharged_hourly[pos:pos + n_today] = di * dt_h
        pos += n_today

        e_ch = float(np.sum(ch) * dt_h)   # MWh drawn from the grid
        e_di = float(np.sum(di) * dt_h)   # MWh injected into the grid
        cost = float(np.sum((arr + spec.charge_cost_eur_mwh) * ch) * dt_h)
        rev = float(np.sum(arr * di) * dt_h)
        cycles = 0.5 * (eta * e_ch + e_di / eta) / e_usable if e_usable > 0 else 0.0

        out.append({
            "date": day, "revenue_eur": rev - cost, "gross_revenue_eur": rev,
            "charge_cost_eur": cost, "mwh_charged": e_ch, "mwh_discharged": e_di,
            "cycles": cycles, "soc_end_mwh": soc, "soc_mean_mwh": float(np.mean(soc_path)),
            "avg_buy_eur_mwh": cost / e_ch if e_ch > 1e-9 else np.nan,
            "avg_sell_eur_mwh": rev / e_di if e_di > 1e-9 else np.nan,
        })

    daily_df = pd.DataFrame(out).set_index("date")
    hourly = {"soc_mwh": soc_hourly, "charged_mwh": charged_hourly, "discharged_mwh": discharged_hourly}
    return daily_df, hourly


def simulate_year(prices: pd.Series, spec: BessSpec, year_index: int = 1,
                  dt_hours: float | None = None, validate: bool = True,
                  cycle_budget_tol: float = 0.01, max_iter: int = 12,
                  lambda_init: float | None = None) -> dict:
    """
    Simulates one operating year.

    The annual cycle budget is enforced through a cycle shadow price
    (lambda, EUR/MWh of throughput) found by bisection: this is the
    equivalent of saying "a cycle costs me X in wear, so I only do it when
    the spread pays for it". A rigid daily limit would not allow 2 cycles on
    a volatile day and 0 on a flat one.
    """
    prices = prepare_price_series(prices)
    if dt_hours is None:
        dt_hours = infer_dt_hours(prices)
    if validate:
        validate_price_series(prices, dt_hours)

    f = spec.year_factors(year_index)
    days = [(d, s.to_numpy(dtype=float)) for d, s in prices.groupby(prices.index.normalize())]
    n_days = len(days)
    budget = MAX_CYCLES_PER_YEAR * n_days / 365.0  # pro-rated for a partial year

    # --- search for the cycle shadow price ---
    # Warm start: last year already found a lambda that is close to right
    # (degradation shifts the optimum only slightly), so try that first.
    lam = 0.0
    if lambda_init and lambda_init > 0:
        trial, trial_hourly = _run_year_at_lambda(days, spec, f, dt_hours, lambda_init)
        tot = trial["cycles"].sum()
        if budget * (1 - cycle_budget_tol) <= tot <= budget * (1 + cycle_budget_tol):
            return _assemble_year(trial, trial_hourly, spec, f, prices, year_index, n_days, lambda_init)

    detail, hourly = _run_year_at_lambda(days, spec, f, dt_hours, 0.0)
    lam = 0.0
    if detail["cycles"].sum() > budget * (1 + cycle_budget_tol):
        lo, hi = 0.0, max(5.0, 2 * (lambda_init or 0.0))
        for _ in range(20):  # widen until the constraint is satisfied
            probe, _ = _run_year_at_lambda(days, spec, f, dt_hours, hi)
            if probe["cycles"].sum() <= budget:
                break
            hi *= 2
        for _ in range(max_iter):
            lam = 0.5 * (lo + hi)
            trial, trial_hourly = _run_year_at_lambda(days, spec, f, dt_hours, lam)
            tot = trial["cycles"].sum()
            if tot > budget:
                lo = lam
            else:
                hi = lam
                detail, hourly = trial, trial_hourly
            if abs(tot - budget) <= budget * cycle_budget_tol:
                detail, hourly = trial, trial_hourly
                break

    return _assemble_year(detail, hourly, spec, f, prices, year_index, n_days, lam)


def _assemble_year(detail: pd.DataFrame, hourly: dict, spec: BessSpec, f: dict,
                   prices: pd.Series, year_index: int, n_days: int, lam: float) -> dict:
    """Applies the post-optimization corrections and builds the output dict."""
    raw_rev = float(detail["revenue_eur"].sum())
    buy_raw = float(detail["charge_cost_eur"].sum())      # gross EUR spent buying
    sell_raw = float(detail["gross_revenue_eur"].sum())   # gross EUR earned selling
    hours = n_days * 24.0
    aux_mwh = spec.aux_load_pct_of_power * spec.power_mw * hours
    aux_cost = aux_mwh * float(prices.mean())

    adj = spec.foresight_factor * spec.availability_pct
    net_rev = (raw_rev * adj) - aux_cost

    e_ch, e_di = float(detail["mwh_charged"].sum()), float(detail["mwh_discharged"].sum())
    hourly_df = pd.DataFrame(hourly, index=prices.index)
    return {
        "year_index": year_index,
        "n_days": n_days,
        "cycle_shadow_price_eur_mwh": lam,
        "energy_mwh": f["energy_mwh"],
        "rte": f["rte"],
        "p_discharge_mw": f["p_discharge_mw"],
        "capacity_factor": f["capacity_factor"],
        "revenue_raw_eur": raw_rev,
        "buy_cost_raw_eur": buy_raw,
        "sell_revenue_raw_eur": sell_raw,
        "adjustment_factor": adj,
        "aux_cost_eur": aux_cost,
        "aux_mwh": aux_mwh,
        "revenue_eur": net_rev,
        "revenue_k_eur_per_mw": (net_rev / 1000.0) / spec.power_mw,
        "revenue_k_eur_per_mwh": (net_rev / 1000.0) / spec.energy_mwh,
        "mwh_charged": e_ch,
        "mwh_discharged": e_di,
        "hours_equivalent": e_di / spec.power_mw,      # full-power-equivalent discharge hours
        "soc_avg_mwh": float(detail["soc_mean_mwh"].mean()),
        "cycles": float(detail["cycles"].sum()),
        "avg_buy_eur_mwh": buy_raw / e_ch if e_ch > 0 else np.nan,
        "avg_sell_eur_mwh": sell_raw / e_di if e_di > 0 else np.nan,
        "avg_price_eur_mwh": float(prices.mean()),
        "daily_detail": detail,
        "hourly_detail": hourly_df,
    }


def split_calendar_years(prices: pd.Series) -> dict[int, pd.Series]:
    """Splits the price series into calendar years and maps them onto
    operating years (1, 2, 3, ...) in chronological order."""
    return {i: prices[prices.index.year == cy]
            for i, cy in enumerate(sorted(prices.index.year.unique()), start=1)}


def simulate_lifetime(prices: pd.Series, spec: BessSpec, price_source: list[int] | None = None,
                      validate: bool = True, progress: bool = True) -> tuple[pd.DataFrame, dict]:
    """
    Computes the revenue of each operating year. Each calendar year found in
    the price series is one operating year, in chronological order. When the
    input comes from Excel the series always covers the 20 years 2029-2048
    (empty year-columns have already been filled by `fill_missing_years`).

    `price_source` (optional) is, for each operating year, the number of the
    Prices year-column its prices come from; it is only reported, not used.

    No discounting (v7): the result is the revenue of each single year.

    Returns (annual table, summary dict with the full hour-by-hour trace for
    every year).
    """
    prices = prepare_price_series(prices)
    dt_h = infer_dt_hours(prices)
    if validate:
        validate_price_series(prices, dt_h)

    calendar_years = sorted(prices.index.year.unique())
    by_year = split_calendar_years(prices)
    n_years = len(by_year)

    rows, hourly_frames, lam_prev = [], [], None
    for y in range(1, n_years + 1):
        py, cy = by_year[y], calendar_years[y - 1]

        r = simulate_year(py, spec, year_index=y, dt_hours=dt_h, validate=False,
                          lambda_init=lam_prev)
        lam_prev = r["cycle_shadow_price_eur_mwh"] or None

        hourly_df = r.pop("hourly_detail")
        n = len(hourly_df)
        hourly_frames.append(pd.DataFrame({
            "year_index": y,
            "calendar_year": int(cy),
            "day": (np.arange(n) // 24) + 1,
            "hour": (np.arange(n) % 24) + 1,
            "price_eur_mwh": py.to_numpy(),
            "soc_mwh": hourly_df["soc_mwh"].to_numpy(),
            "bought_mwh": hourly_df["charged_mwh"].to_numpy(),
            "sold_mwh": hourly_df["discharged_mwh"].to_numpy(),
        }))

        r.pop("daily_detail")
        r["calendar_year"] = int(cy)
        r["prices_from_year"] = price_source[y - 1] if price_source else y
        rows.append(r)
        if progress:
            print(f"  year {y:>2}/{n_years} ({cy}): {r['revenue_k_eur_per_mw']:7.1f} k EUR/MW  "
                  f"({r['cycles']:.0f} cycles, {r['n_days']} days, "
                  f"capacity {r['capacity_factor']:.1%}, prices of Year {r['prices_from_year']})")

    df = pd.DataFrame(rows).set_index("year_index")
    hourly_all = pd.concat(hourly_frames, ignore_index=True)
    n_filled = len(set(df["prices_from_year"]))
    summary = {
        "n_years": n_years,
        "n_price_columns": n_filled,
        "revenue_avg_k_eur_per_mw": df["revenue_k_eur_per_mw"].mean(),
        "revenue_y1_k_eur_per_mw": df["revenue_k_eur_per_mw"].iloc[0],
        "cycles_avg": df["cycles"].mean(),
        "hourly_all": hourly_all,
    }
    return df, summary


def example_day_profile(prices: pd.Series, spec: BessSpec, date: str,
                        year_index: int = 1, cycle_price: float = 0.0) -> pd.DataFrame:
    """Hourly profile of one sample day (price, charge, discharge, SOC) for
    the chart. This is an isolated, illustrative day: it starts from the
    minimum SOC, not from the start-of-life SOC."""
    prices = prepare_price_series(prices)
    dt_h = infer_dt_hours(prices)
    f = spec.year_factors(year_index)
    day = prices.loc[date]
    e_usable = f["energy_mwh"]
    eta = np.sqrt(f["rte"])
    r = optimize_window(
        prices=day.to_numpy(dtype=float), dt=dt_h, e_usable=e_usable,
        soc_min=spec.soc_min_frac * e_usable, soc_max=spec.soc_max_frac * e_usable,
        soc_start=spec.soc_min_frac * e_usable, p_ch=f["p_charge_mw"],
        p_dis=f["p_discharge_mw"], eta_c=eta, eta_d=eta,
        cost_ch=spec.charge_cost_eur_mwh,
        cycle_price=cycle_price, max_cycles=MAX_CYCLES_PER_DAY,
    )
    return pd.DataFrame({
        "hour": [t.strftime("%H:%M") for t in day.index],
        "price_eur_mwh": day.to_numpy(),
        "charge_mw": r["charge_mw"],
        "discharge_mw": r["discharge_mw"],
        "soc_mwh": r["soc_mwh"],
    })


# ---------------------------------------------------------------------------
# 5. Synthetic price generator (demo/test ONLY)
# ---------------------------------------------------------------------------

def _synthetic_shape(hour: np.ndarray, doy: np.ndarray, level: float, amplitude: float,
                     seasonal_amplitude: float, noise: np.ndarray) -> np.ndarray:
    """Shared hourly/seasonal shape used by both synthetic generators below."""
    daily = -np.cos(2 * np.pi * (hour - 3) / 24)
    solar = -0.8 * np.exp(-((hour - 13) ** 2) / (2 * 3.5 ** 2))
    seasonal = np.sin(2 * np.pi * (doy - 15) / 365)
    return np.clip(level + amplitude * daily + amplitude * 0.6 * solar
                   + seasonal_amplitude * seasonal + noise, -30, None)


def make_synthetic_prices(year: int = 2027, n_years: int = 1, mean_price: float = 95.0,
                          daily_amplitude: float = 45.0, seasonal_amplitude: float = 20.0,
                          noise_std: float = 12.0, level_drift_pct: float = -0.005,
                          spread_drift_pct: float = 0.01, seed: int = 42) -> pd.Series:
    """SYNTHETIC hourly series with a plausible shape for the Italian
    market, over one or more years, on a real calendar (Europe/Rome). Do NOT
    use for real estimates."""
    rng = np.random.default_rng(seed)
    chunks = []
    for k in range(n_years):
        y = year + k
        idx = pd.date_range(f"{y}-01-01", f"{y}-12-31 23:00", freq="h", tz=TZ)
        p = _synthetic_shape(
            idx.hour.to_numpy(), idx.dayofyear.to_numpy(),
            level=mean_price * (1 + level_drift_pct) ** k,
            amplitude=daily_amplitude * (1 + spread_drift_pct) ** k,
            seasonal_amplitude=seasonal_amplitude,
            noise=rng.normal(0, noise_std, len(idx)),
        )
        chunks.append(pd.Series(p, index=idx))
    return pd.concat(chunks).rename("price_eur_mwh")


def make_synthetic_matrix(n_years: int = 20, mean_price: float = 95.0,
                          daily_amplitude: float = 45.0, seasonal_amplitude: float = 20.0,
                          noise_std: float = 12.0, level_drift_pct: float = -0.005,
                          spread_drift_pct: float = 0.01, seed: int = 42) -> np.ndarray:
    """SYNTHETIC price matrix in "typical calendar" form: 8,760 rows
    (365 days x 24 hours) x `n_years` columns. For the demo template ONLY."""
    rng = np.random.default_rng(seed)
    hour = np.tile(np.arange(24), 365)
    doy = np.repeat(np.arange(1, 366), 24)
    cols = [
        _synthetic_shape(hour, doy,
                         level=mean_price * (1 + level_drift_pct) ** k,
                         amplitude=daily_amplitude * (1 + spread_drift_pct) ** k,
                         seasonal_amplitude=seasonal_amplitude,
                         noise=rng.normal(0, noise_std, len(hour)))
        for k in range(n_years)
    ]
    return np.column_stack(cols)


def make_flat_capacity_table(n_years: int = LIFETIME_YEARS) -> pd.DataFrame:
    """Default capacity table: no degradation (100% every year). Used as the
    template's starting point, since the table is mandatory."""
    return pd.DataFrame({"year": np.arange(1, n_years + 1), "capacity_pct": 1.0})


def make_flat_rte_table(n_years: int = LIFETIME_YEARS, rte: float = 0.87) -> pd.DataFrame:
    """Default RTE table: the same RTE (87%) every year. Used as the
    template's starting point, to be replaced with the supplier's curve."""
    return pd.DataFrame({"year": np.arange(1, n_years + 1), "rte": rte})
