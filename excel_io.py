"""
Excel interface for the BESS model: input template, parameter and price
reading, output report writing with charts.

v7: two yearly tables in Parameters (CAPACITY DEGRADATION in % of nameplate,
and RTE = the asset's actual RTE each year), no 'RTE year 1' input, no power
degradation; no discount rate / NPV, no 'Useful life', no 'Calendar year of year 1'
(year 1 is always 2029, always 20 years), no charge on injection. Empty
price year-columns repeat the last filled year before them.

v6: the cycle limits are no longer inputs (fixed in bess_model.py: 365/year,
2/day) and input cells use a softer yellow. Otherwise unchanged from v4.

v4 simplification: one input path only (price matrix inside the workbook,
no external CSV), one degradation mechanism only (a mandatory table embedded
in the Parameters sheet, no separate optional sheet, no %/year rates, no
augmentation), and four output sheets instead of seven (Summary,
Annual_Results, Raw_Data, Sample_Day).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.marker import Marker
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.text import RichText, Text
from openpyxl.chart.title import Title
from openpyxl.drawing.text import (CharacterProperties, Font as DFont, Paragraph,
                                   ParagraphProperties, RegularTextRun, RichTextProperties)
from openpyxl.drawing.line import LineProperties
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from bess_model import (LIFETIME_YEARS, START_YEAR, BessSpec, fill_missing_years,
                        make_flat_capacity_table, make_flat_rte_table, make_synthetic_matrix,
                        matrix_to_series)

FONT = "Arial"
BLUE = Font(name=FONT, color="0000FF", size=10)          # editable input
BLACK = Font(name=FONT, color="000000", size=10)
BOLD = Font(name=FONT, bold=True, size=10)
TITLE = Font(name=FONT, bold=True, size=14, color="1F3864")
SECT = Font(name=FONT, bold=True, size=10, color="1F3864")
SMALL = Font(name=FONT, size=9, color="595959")
HDR_FILL = PatternFill("solid", fgColor="1F3864")
HDR_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=10)
YELLOW = PatternFill("solid", fgColor="FFFBE0")   # soft pale yellow for input cells
GREY = PatternFill("solid", fgColor="F2F2F2")
THIN = Border(*[Side(style="thin", color="BFBFBF")] * 4)

MAX_YEARS = LIFETIME_YEARS

# A soft, muted palette instead of Excel's bold theme defaults, applied
# together with a thin line width so charts read cleanly with 20 categories.
# Soft pastel palette: blue, orange, green, grey.
CHART_COLORS = ["7FA7D9", "F4B183", "A9D18E", "BFBFBF"]
LINE_WIDTH_EMU = 28575  # 2.25pt: pastel lines need some thickness to stay visible
GRID_COLOR = "E7E6E6"   # very light gridlines
AXIS_COLOR = "BFBFBF"
TEXT_COLOR = "595959"
CHART_W, CHART_H = 12.6, 7.6   # cm, four charts side by side

# key, label, default, unit, number format, note
PARAMS = [
    ("__SEC__", "SIZING", None, None, None, None),
    ("power_mw", "Nominal power", 1.0, "MW", "#,##0.0#",
     "Set to 1 to reason per installed MW (revenue comes out directly in k EUR/MW)"),
    ("duration_h", "Duration (discharge hours)", 8.0, "h", "0.0",
     "Installed energy = Power x Duration. This is the SOC ceiling"),
    ("power_charge_mw", "Max charging power", None, "MW", "#,##0.0#", "Leave blank = same as nominal"),
    ("power_discharge_mw", "Max discharging power", None, "MW", "#,##0.0#", "Leave blank = same as nominal"),
    ("poi_limit_mw", "Power at the grid connection point (POI)", None, "MW", "#,##0.0#",
     "Leave blank if not binding"),

    ("__SEC__", "OPERATING WINDOW", None, None, None, None),
    ("soc_min_frac", "Minimum SOC", 0.05, "%", "0.0%", "No BESS is operated down to 0%"),
    ("soc_max_frac", "Maximum SOC", 0.95, "%", "0.0%", ""),
    ("soc_start_frac", "Starting SOC", 0.95, "%", "0.0%",
     "Default: battery delivered FULL (= maximum SOC). Only applies to the first day"),

    ("__SEC__", "LOSSES AND AVAILABILITY", None, None, None, None),
    ("aux_load_pct_of_power", "Auxiliary consumption (HVAC, etc.)", 0.005, "% of power", "0.00%", "Continuous load"),
    ("availability_pct", "Availability", 0.98, "%", "0.0%", "Planned maintenance + outages"),

    ("__SEC__", "VARIABLE MARKET COSTS", None, None, None, None),
    ("charge_cost_eur_mwh", "Charges on withdrawal (charging)", 0.0, "EUR/MWh", "#,##0.00",
     "Transmission, dispatching, etc."),

    ("__SEC__", "OPERATIONAL REALISM", None, None, None, None),
    ("foresight_factor", "Foresight factor", 0.85, "%", "0.0%",
     "Haircut on perfect-foresight optimization (typ. 0.80-0.90)"),

    ("__SEC__", "SCENARIO", None, None, None, None),
    ("example_day", "Sample day for the chart", "2029-06-15", "date", "@",
     "Format YYYY-MM-DD, between 2029-01-01 and 2048-12-31"),
]

CAP_HEADER = "Capacity (%)"   # header of the CAPACITY DEGRADATION table (column C)
RTE_HEADER = "RTE (%)"        # header of the RTE table (column C)


# ---------------------------------------------------------------------------
# Chart styling helpers
# ---------------------------------------------------------------------------

def _thin_line(series, color: str) -> None:
    """Thin, marker-free line so 20 yearly categories stay readable."""
    gp = GraphicalProperties()
    gp.line = LineProperties(w=LINE_WIDTH_EMU, solidFill=color)
    series.graphicalProperties = gp
    series.marker = Marker(symbol="none")
    series.smooth = False


def _cp(size: int = 800, bold: bool = False, color: str = TEXT_COLOR) -> CharacterProperties:
    return CharacterProperties(sz=size, b=bold, solidFill=color, latin=DFont(typeface=FONT))


def _rich(size: int = 800, bold: bool = False, color: str = TEXT_COLOR) -> RichText:
    cp = _cp(size, bold, color)
    return RichText(bodyPr=RichTextProperties(), p=[Paragraph(pPr=ParagraphProperties(defRPr=cp), endParaRPr=cp)])


def _title(text: str, size: int = 1100, bold: bool = True, color: str = "404040") -> Title:
    """Title outside the plot area, on a white background."""
    cp = _cp(size, bold, color)
    rich = RichText(bodyPr=RichTextProperties(),
                    p=[Paragraph(pPr=ParagraphProperties(defRPr=cp), r=[RegularTextRun(rPr=cp, t=text)])])
    t = Title(tx=Text(rich=rich), overlay=False)
    t.spPr = GraphicalProperties(solidFill="FFFFFF")
    return t


def _style_axis(ax, title: str | None = None, fmt: str | None = None, grid: bool = False) -> None:
    ax.delete = False                       # openpyxl >= 3.1 hides axes by default
    ax.txPr = _rich(800)
    ax.spPr = GraphicalProperties(ln=LineProperties(solidFill=AXIS_COLOR, w=6350))
    ax.majorTickMark = "none"
    ax.minorTickMark = "none"
    ax.majorGridlines = (ChartLines(spPr=GraphicalProperties(ln=LineProperties(solidFill=GRID_COLOR, w=6350)))
                         if grid else None)
    if fmt:
        ax.number_format = fmt
        ax.numFmt.sourceLinked = False
    if title:
        ax.title = _title(title, size=800, bold=False, color=TEXT_COLOR)


def _style_chart(chart, title: str, y_title: str | None = None, y_fmt: str | None = None,
                 legend: bool = False, height: float = CHART_H, width: float = CHART_W) -> None:
    """Clean chart: no border, white title, light grid, readable axes, years on x."""
    chart.height, chart.width = height, width
    chart.title = _title(title)
    chart.varyColors = False
    chart.graphical_properties = GraphicalProperties(solidFill="FFFFFF", ln=LineProperties(noFill=True))
    chart.plot_area.graphicalProperties = GraphicalProperties(noFill=True, ln=LineProperties(noFill=True))
    _style_axis(chart.x_axis)
    chart.x_axis.tickLblSkip = 2
    _style_axis(chart.y_axis, y_title, y_fmt, grid=True)
    if legend:
        chart.legend.position = "b"
        chart.legend.overlay = False
        chart.legend.txPr = _rich(800)
    else:
        chart.legend = None


def _bars(chart, colors) -> None:
    chart.gapWidth = 60
    for sr, color in zip(chart.series, colors):
        sr.graphicalProperties = GraphicalProperties(solidFill=color, ln=LineProperties(noFill=True))
        sr.invertIfNegative = False


# ---------------------------------------------------------------------------
# Input template
# ---------------------------------------------------------------------------

def _style_header(ws, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill, cell.font, cell.border = HDR_FILL, HDR_FONT, THIN
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _write_year_table(ws, start_row: int, title: str, note: str, header: str,
                      values: list[float]) -> int:
    """Writes one yearly table (Year | value) inside the Parameters sheet,
    columns B-C. Returns the row after the table."""
    r = start_row
    ws.cell(row=r, column=2, value=title).font = SECT
    for c in range(2, 6):
        ws.cell(row=r, column=c).fill = GREY
    r += 1
    ws.cell(row=r, column=2, value=note).font = SMALL
    r += 1
    ws.cell(row=r, column=2, value="Year")
    ws.cell(row=r, column=3, value=header)
    _style_header(ws, r, 3)
    r += 1
    for k, v in enumerate(values, start=1):
        ws.cell(row=r, column=2, value=k).font = BLACK
        c = ws.cell(row=r, column=3, value=float(v))
        c.font, c.fill, c.border = BLUE, YELLOW, THIN
        c.number_format = "0.0%"
        r += 1
    return r


def _write_year_tables(ws, start_row: int, n_years: int = MAX_YEARS) -> int:
    """Writes the two mandatory yearly tables: CAPACITY DEGRADATION (100% and
    down) and RTE (the asset's actual RTE, e.g. 87% and down)."""
    r = _write_year_table(
        ws, start_row, "CAPACITY DEGRADATION (mandatory - one row per year)",
        "Energy capacity as % of nameplate: starts at 100% and goes down (supplier's guaranteed curve).",
        CAP_HEADER, make_flat_capacity_table(n_years)["capacity_pct"].tolist())
    r += 1
    r = _write_year_table(
        ws, r, "RTE (mandatory - one row per year)",
        "The asset's ACTUAL round-trip efficiency in that year (e.g. 87%, 86%, ...), not a % of year 1.",
        RTE_HEADER, make_flat_rte_table(n_years)["rte"].tolist())
    return r


def create_input_template(path: str, with_demo_prices: bool = True,
                          n_years: int = MAX_YEARS) -> str:
    """
    Creates the input file. The Prices sheet is a MATRIX: column A = day of
    year (1-365), column B = hour (1-24), columns C onward = one operating
    year each (Year 1 = 2029 ... Year 20 = 2048), 8,760 rows. The headers are
    always written for all 20 years; `n_years` is only how many of them are
    filled with demo prices.
    """
    n_years = max(1, min(int(n_years), MAX_YEARS))
    wb = Workbook()

    # --- Instructions ---
    ws = wb.active
    ws.title = "Instructions"
    ws.sheet_view.showGridLines = False
    ws["B2"] = "BESS productivity model - input file"
    ws["B2"].font = TITLE
    text = [
        "",
        "HOW TO USE IT",
        "1. 'Parameters' sheet: fill in the YELLOW cells. They are the only ones you should touch,",
        "   including the two mandatory yearly tables at the bottom of the sheet:",
        "   - CAPACITY DEGRADATION: energy capacity as % of nameplate, 100% and down;",
        "   - RTE: the asset's actual round-trip efficiency each year (e.g. 87% and down).",
        "2. 'Prices' sheet: paste your zonal day-ahead hourly prices (EUR/MWh).",
        "   Layout: column A = day of year (1-365), column B = hour (1-24), one COLUMN PER YEAR",
        "   ('Year 1' = 2029, 'Year 2' = 2030, ... 'Year 20' = 2048). Each column is 8,760 values.",
        "   WARNING: use the prices of YOUR market zone (NORD, CNOR, CSUD, SUD, SICI, SARD).",
        "3. Run:  python run_bess.py input_bess.xlsx risultati_bess.xlsx",
        "",
        "TIME FRAME (fixed)",
        "The model always runs 20 operating years, 2029 to 2048. 'Year 1' is always 2029.",
        "'Year 1' is mandatory. Any year-column left EMPTY takes the prices of the last filled",
        "year before it: e.g. only 'Year 1' filled = 2029 repeated for 20 years; 'Year 1'-'Year 5'",
        "filled = years 6-20 repeat 'Year 5'.",
        "",
        "NEGATIVE PRICES",
        "In every hour with a negative price the battery can only charge or stay idle, never discharge.",
        "",
        "CALENDAR",
        "The Prices sheet is a TYPICAL calendar: 365 days of 24 hours, no daylight saving time, no",
        "leap years.",
        "",
        "OPTIMIZATION HORIZON (fixed, not a parameter)",
        "Every day, the model looks at that day plus the next one before deciding, but only commits",
        "the current day's decision. This is fixed at 2 days and cannot be changed here: it exists",
        "only to stop the model from making a bad call right at midnight (e.g. draining the battery",
        "at 23:00 when a much better buying window opens at 3am the next day).",
        "",
        "WHAT IT COMPUTES",
        "Day-ahead arbitrage revenue: for each day, it decides which hours to charge and which to",
        "discharge in order to maximise the margin, respecting power, capacity, the SOC window,",
        "efficiency and the fixed cycle limits (365/year, 2/day). It repeats this for each of the 20",
        "years 2029-2048, with that year's capacity and RTE from the two tables.",
        "",
        "WHAT IT DOES NOT COMPUTE (add these separately, in the business plan)",
        "- Any revenue other than day-ahead arbitrage",
        "- CAPEX, OPEX, taxes, imbalance costs",
        "The result is the GROSS market revenue from arbitrage of each year, in k EUR/MW (not discounted).",
        "",
        "COLOR KEY",
        "Yellow cells with blue text = input to fill in.   White cells = fixed or computed.",
    ]
    for i, t in enumerate(text, start=3):
        ws.cell(row=i, column=2, value=t)
        ws.cell(row=i, column=2).font = BOLD if t.isupper() and t else Font(name=FONT, size=10)
    ws.column_dimensions["A"].width = 2
    ws.column_dimensions["B"].width = 105

    # --- Parameters ---
    ws = wb.create_sheet("Parameters")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "key"
    for j, h in enumerate(["Parameter", "Value", "Unit", "Note"], start=2):
        ws.cell(row=1, column=j, value=h)
    _style_header(ws, 1, 5)
    ws.column_dimensions["A"].hidden = True

    r = 2
    for key, label, default, unit, fmt, note in PARAMS:
        if key == "__SEC__":
            ws.cell(row=r, column=2, value=label).font = SECT
            for c in range(2, 6):
                ws.cell(row=r, column=c).fill = GREY
            r += 1
            continue
        ws.cell(row=r, column=1, value=key)
        ws.cell(row=r, column=2, value=label).font = BLACK
        v = ws.cell(row=r, column=3, value=default)
        v.font, v.fill, v.border = BLUE, YELLOW, THIN
        v.number_format = fmt
        ws.cell(row=r, column=4, value=unit).font = BLACK
        ws.cell(row=r, column=5, value=note).font = SMALL
        r += 1

    r += 1
    _write_year_tables(ws, r, MAX_YEARS)

    for col, w in zip("ABCDE", [14, 42, 16, 18, 56]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"

    # --- Prices (matrix: day x hour on the rows, one year per column) ---
    ws = wb.create_sheet("Prices")
    ws.cell(row=1, column=1, value="day")
    ws.cell(row=1, column=2, value="hour")
    for k in range(MAX_YEARS):
        ws.cell(row=1, column=3 + k, value=f"Year {k + 1}")
    _style_header(ws, 1, 2 + MAX_YEARS)

    day = np.repeat(np.arange(1, 366), 24)
    hour = np.tile(np.arange(1, 25), 365)
    demo = np.round(make_synthetic_matrix(n_years), 2) if with_demo_prices else None

    # Row-by-row, no per-cell formatting: styling 175k+ cells would bloat
    # the file and make writing very slow.
    for i in range(8760):
        row = i + 2
        ws.cell(row=row, column=1, value=int(day[i]))
        ws.cell(row=row, column=2, value=int(hour[i]))
        if demo is not None:
            for k in range(n_years):
                ws.cell(row=row, column=3 + k, value=float(demo[i, k]))

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 7
    for k in range(MAX_YEARS):
        ws.column_dimensions[get_column_letter(3 + k)].width = 11
    ws.freeze_panes = "C2"

    if with_demo_prices:
        note = [
            "WARNING: DEMO prices, synthetic and not real.",
            f"Filled for {n_years} of 20 years: one column per operating year, 8,760 rows (365 x 24).",
            "Replace them with your own zonal curve (historical GME data or a scenario forecast).",
            "Empty year-columns repeat the last filled year before them.",
        ]
        for k, t in enumerate(note, start=2):
            c = ws.cell(row=k, column=4 + MAX_YEARS, value=t)
            c.font = Font(name=FONT, size=10, color="C00000", bold=(k == 2))
        ws.column_dimensions[get_column_letter(4 + MAX_YEARS)].width = 70

    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# Reading the input
# ---------------------------------------------------------------------------

def _num(v, default=None):
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    return float(v)


def _read_year_table(rows: list, header: str, name: str) -> list[float]:
    """Finds a yearly table inside the Parameters sheet (`rows` = its rows as
    value tuples) by its header row (column B = 'Year', column C = `header`)
    and reads one value per year, until the first empty Year cell."""
    header_idx = None
    for i, row in enumerate(rows):
        if len(row) >= 3 and row[1] == "Year" and row[2] == header:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"Could not find the {name} table in the Parameters sheet "
                         f"(expected a 'Year' | '{header}' header). Please re-generate the template.")
    values = []
    for row in rows[header_idx + 1:]:
        y = row[1] if len(row) > 1 else None
        if y is None:
            break
        v = row[2] if len(row) > 2 else None
        if v is None:
            raise ValueError(f"The {name} table is missing the value for year {y}. It is mandatory: "
                             "fill it in, or repeat the previous year's value if nothing changes.")
        v = float(v)
        if not 0 < v <= 1:
            raise ValueError(f"The {name} table has {v} for year {y}: write it as a percentage "
                             "(e.g. 87%), between 0% and 100%.")
        values.append(v)
    if not values:
        raise ValueError(f"The {name} table is empty. It is mandatory: fill in one row per year.")
    return values


def _read_price_matrix(ws) -> tuple[pd.Series, list[int]]:
    """Reads the Prices sheet (day/hour matrix, one column per year, 'Year 1'
    = 2029 ... 'Year 20' = 2048) and converts it into a continuous 20-year
    price series. Each column is placed by the number in its header, so an
    empty column never shifts the years after it. Empty years take the
    prices of the last filled year before them (`fill_missing_years`).
    Returns the series and, for each year, the column its prices come from."""
    rows = list(ws.iter_rows(min_row=1, values_only=True))
    if len(rows) < 2:
        raise ValueError("The 'Prices' sheet is empty.")
    header = list(rows[0])

    year_cols = {}   # operating year number -> sheet column index
    for j, name in enumerate(header):
        lab = str(name).strip().lower() if name is not None else ""
        if lab.startswith("year"):
            try:
                k = int(lab.split()[-1])
            except ValueError:
                continue
            if 1 <= k <= MAX_YEARS:
                year_cols[k] = j
    if 1 not in year_cols:
        raise ValueError("Could not find the 'Year 1' column in the 'Prices' sheet "
                         "(expected headers 'Year 1', 'Year 2', ... 'Year 20').")

    data = []
    for row in rows[1:]:
        vals = [row[year_cols[k]] if k in year_cols and year_cols[k] < len(row) else None
                for k in range(1, MAX_YEARS + 1)]
        if all(v is None for v in vals):
            continue  # blank row: end of data, or a gap at the bottom of the sheet
        data.append(vals)
    if not data:
        raise ValueError("The 'Prices' sheet has no values.")

    arr = np.array(data, dtype=object)
    columns = []
    for k in range(MAX_YEARS):
        col = arr[:, k]
        if all(v is None for v in col):
            columns.append(None)       # empty year: filled below with the last filled one
            continue
        num = pd.to_numeric(pd.Series(col), errors="coerce").to_numpy(dtype=float)
        if np.isnan(num).any():
            raise ValueError(f"'Year {k + 1}' in the 'Prices' sheet has {int(np.isnan(num).sum())} "
                             "empty or non-numeric cells: complete it or empty it entirely.")
        columns.append(num)

    matrix, source = fill_missing_years(columns)
    return matrix_to_series(matrix, start_year=START_YEAR), source


def read_input(path: str) -> tuple[BessSpec, pd.Series, dict]:
    wb = load_workbook(path, data_only=True, read_only=True)

    if "Parameters" not in wb.sheetnames or "Prices" not in wb.sheetnames:
        raise ValueError("The file must contain the 'Parameters' and 'Prices' sheets.")

    p: dict = {}
    param_rows = list(wb["Parameters"].iter_rows(min_row=1, max_col=5, values_only=True))
    for row in param_rows[1:]:
        if row and row[0]:
            p[str(row[0]).strip()] = row[2]

    cap = _read_year_table(param_rows, CAP_HEADER, "CAPACITY DEGRADATION")
    rte = _read_year_table(param_rows, RTE_HEADER, "RTE")
    cap_table = pd.DataFrame({"year": np.arange(1, len(cap) + 1), "capacity_pct": cap})
    rte_table = pd.DataFrame({"year": np.arange(1, len(rte) + 1), "rte": rte})

    spec = BessSpec(
        power_mw=_num(p.get("power_mw"), 1.0),
        duration_h=_num(p.get("duration_h"), 8.0),
        power_charge_mw=_num(p.get("power_charge_mw")),
        power_discharge_mw=_num(p.get("power_discharge_mw")),
        poi_limit_mw=_num(p.get("poi_limit_mw")),
        soc_min_frac=_num(p.get("soc_min_frac"), 0.05),
        soc_max_frac=_num(p.get("soc_max_frac"), 0.95),
        soc_start_frac=_num(p.get("soc_start_frac"), 0.95),
        capacity_table=cap_table,
        rte_table=rte_table,
        aux_load_pct_of_power=_num(p.get("aux_load_pct_of_power"), 0.005),
        availability_pct=_num(p.get("availability_pct"), 0.98),
        charge_cost_eur_mwh=_num(p.get("charge_cost_eur_mwh"), 0.0),
        foresight_factor=_num(p.get("foresight_factor"), 0.85),
    )

    prices, source = _read_price_matrix(wb["Prices"])
    wb.close()

    ex_day = p.get("example_day")
    scenario = {
        "price_source": source,
        "example_day": str(ex_day)[:10] if ex_day else None,
        "params_raw": p,
    }
    return spec, prices, scenario


# ---------------------------------------------------------------------------
# Writing the output report
# ---------------------------------------------------------------------------

ANNUAL_COLS = [
    ("year_index", "Operating year", "0"),
    ("calendar_year", "Calendar year", "0"),
    ("capacity_factor", "Capacity (% of nameplate)", "0.0%"),
    ("energy_mwh", "Energy capacity (MWh)", "#,##0.0#"),
    ("rte", "RTE", "0.0%"),
    ("p_discharge_mw", "Discharge power (MW)", "#,##0.0#"),
    ("cycles", "Equivalent cycles", "#,##0"),
    ("mwh_charged", "MWh bought", "#,##0"),
    ("mwh_discharged", "MWh sold", "#,##0"),
    ("hours_equivalent", "Equivalent discharge hours", "#,##0"),
    ("avg_buy_eur_mwh", "Average buy price (EUR/MWh)", "#,##0.0"),
    ("avg_sell_eur_mwh", "Average sell price (EUR/MWh)", "#,##0.0"),
    ("spread_eur_mwh", "Captured spread (EUR/MWh)", "#,##0.0"),
    ("cycle_shadow_price_eur_mwh", "Cycle shadow price (EUR/MWh)", "#,##0.00"),
    ("bought_k_eur", "Bought (k EUR)", "#,##0.0"),
    ("sold_k_eur", "Sold (k EUR)", "#,##0.0"),
    ("aux_k_eur", "Auxiliary cost (k EUR)", "#,##0.0"),
    ("revenue_k_eur", "Net revenue (k EUR)", "#,##0.0"),
    ("revenue_k_eur_per_mw", "Net revenue (k EUR/MW)", "#,##0.0"),
    ("revenue_k_eur_per_mwh", "Net revenue (k EUR/MWh)", "#,##0.0"),
]

RAW_COLS = [
    ("year_index", "Operating year", "0"),
    ("calendar_year", "Calendar year", "0"),
    ("day", "Day", "0"),
    ("hour", "Hour", "0"),
    ("soc_mwh", "SOC (MWh)", "#,##0.000"),
    ("bought_mwh", "Energy bought (MWh)", "#,##0.000"),
    ("sold_mwh", "Energy sold (MWh)", "#,##0.000"),
]


def _write_table(ws, df: pd.DataFrame, cols, start_row: int = 1, style_rows: bool = True) -> int:
    for j, (_, label, _) in enumerate(cols, start=1):
        ws.cell(row=start_row, column=j, value=label)
    _style_header(ws, start_row, len(cols))
    for i, (_, rec) in enumerate(df.iterrows(), start=start_row + 1):
        for j, (key, _, fmt) in enumerate(cols, start=1):
            v = rec.get(key)
            if isinstance(v, float) and np.isnan(v):
                v = None
            cell = ws.cell(row=i, column=j, value=v)
            cell.number_format = fmt
            if style_rows:
                cell.font, cell.border = BLACK, THIN
    for j, (_, label, _) in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(j)].width = max(11, min(30, len(label) + 3))
    if style_rows:
        ws.freeze_panes = ws.cell(row=start_row + 1, column=3).coordinate
    return start_row + len(df)


def _write_raw_data(ws, hourly_all: pd.DataFrame) -> None:
    """Writes the hour-by-hour model output for every year: SOC, energy
    bought, energy sold. This is the direct, ungrouped output of the engine.
    No per-cell styling (only the header): with up to 175,200 rows this
    keeps the file a manageable size and the write fast."""
    ws.sheet_view.showGridLines = False
    for j, (_, label, _) in enumerate(RAW_COLS, start=1):
        ws.cell(row=1, column=j, value=label)
    _style_header(ws, 1, len(RAW_COLS))

    cols = [hourly_all[key].to_numpy() for key, _, _ in RAW_COLS]
    for i in range(len(hourly_all)):
        row = i + 2
        for j, arr in enumerate(cols, start=1):
            ws.cell(row=row, column=j, value=arr[i].item() if hasattr(arr[i], "item") else arr[i])

    for j, (_, _, fmt) in enumerate(RAW_COLS, start=1):
        col = get_column_letter(j)
        ws.column_dimensions[col].width = 13
    ws.freeze_panes = "C2"


def write_output(path: str, annual: pd.DataFrame, summary: dict, spec: BessSpec,
                 scenario: dict, day_profile: pd.DataFrame | None) -> str:
    annual = annual.reset_index()
    annual["spread_eur_mwh"] = annual["avg_sell_eur_mwh"] - annual["avg_buy_eur_mwh"]
    adj = spec.foresight_factor * spec.availability_pct
    annual["bought_k_eur"] = -annual["buy_cost_raw_eur"] * adj / 1000.0
    annual["sold_k_eur"] = annual["sell_revenue_raw_eur"] * adj / 1000.0
    annual["aux_k_eur"] = -annual["aux_cost_eur"] / 1000.0
    annual["revenue_k_eur"] = annual["bought_k_eur"] + annual["sold_k_eur"] + annual["aux_k_eur"]
    n = len(annual)

    wb = Workbook()

    # ================= Annual_Results =================
    ws_ann = wb.active
    ws_ann.title = "Annual_Results"
    ws_ann.sheet_view.showGridLines = False
    last = _write_table(ws_ann, annual, ANNUAL_COLS)

    def col_of(key):
        return get_column_letter([c[0] for c in ANNUAL_COLS].index(key) + 1)

    def ref(key):
        return f"Annual_Results!${col_of(key)}$2:${col_of(key)}${last}"

    tot = last + 1
    ws_ann.cell(row=tot, column=1, value="TOTAL").font = BOLD
    fmts = {a: cc for a, _, cc in ANNUAL_COLS}
    for key in ["mwh_charged", "mwh_discharged", "bought_k_eur", "sold_k_eur", "aux_k_eur",
                "revenue_k_eur", "cycles"]:
        c = ws_ann.cell(row=tot, column=[x[0] for x in ANNUAL_COLS].index(key) + 1)
        c.value = f"=SUM({col_of(key)}2:{col_of(key)}{last})"
        c.font, c.number_format = BOLD, fmts[key]

    # ================= Summary =================
    ws = wb.create_sheet("Summary", 0)
    ws.sheet_view.showGridLines = False
    ws["B2"] = "BESS - results summary"
    ws["B2"].font = TITLE
    ws["B3"] = (f"{spec.power_mw:,.1f} MW / {spec.duration_h:,.1f} h "
                f"({spec.energy_mwh:,.1f} MWh) - RTE year 1 {spec.year_factors(1)['rte']:.1%}")
    ws["B3"].font = Font(name=FONT, size=11, color="595959")
    n_cols = summary.get("n_price_columns", n)
    ws["B4"] = (f"{n} years ({int(annual['calendar_year'].min())}-{int(annual['calendar_year'].max())}) - "
                f"{n_cols} price year-column(s) filled"
                + ("" if n_cols >= n else ", empty years repeat the last filled one"))
    ws["B4"].font = Font(name=FONT, size=10, italic=True, color="595959")

    kpis = [
        ("Year 1 revenue", f"=INDEX({ref('revenue_k_eur_per_mw')},1)", "k EUR/MW/year", "#,##0.0"),
        ("Average annual revenue", f"=AVERAGE({ref('revenue_k_eur_per_mw')})", "k EUR/MW/year", "#,##0.0"),
        ("Average annual revenue per installed MWh", f"=AVERAGE({ref('revenue_k_eur_per_mwh')})",
         "k EUR/MWh/year", "#,##0.0"),
        ("", None, None, None),
        ("Average equivalent cycles/year", f"=AVERAGE({ref('cycles')})", "cycles", "#,##0"),
        ("Average equivalent discharge hours/year", f"=AVERAGE({ref('hours_equivalent')})", "h", "#,##0"),
        ("Average captured spread", f"=AVERAGE({ref('spread_eur_mwh')})", "EUR/MWh", "#,##0.0"),
        ("Average buy price", f"=AVERAGE({ref('avg_buy_eur_mwh')})", "EUR/MWh", "#,##0.0"),
        ("Average sell price", f"=AVERAGE({ref('avg_sell_eur_mwh')})", "EUR/MWh", "#,##0.0"),
        ("Total energy sold", f"=SUM({ref('mwh_discharged')})", "MWh", "#,##0"),
        ("", None, None, None),
        ("Remaining capacity at end of life", f"=INDEX({ref('capacity_factor')},{n})", "%", "0.0%"),
        ("Last-year revenue", f"=INDEX({ref('revenue_k_eur_per_mw')},{n})", "k EUR/MW/year", "#,##0.0"),
    ]
    r = 5
    for j, h in enumerate(["INDICATOR", "VALUE", "UNIT"], start=2):
        ws.cell(row=r, column=j, value=h)
    _style_header(ws, r, 4)
    for label, val, unit, fmt in kpis:
        r += 1
        if not label:
            continue
        ws.cell(row=r, column=2, value=label).font = BLACK
        c = ws.cell(row=r, column=3, value=val)
        c.font, c.number_format = Font(name=FONT, bold=True, size=10), fmt
        c.alignment = Alignment(horizontal="right")
        ws.cell(row=r, column=4, value=unit).font = SMALL
        for cc in range(2, 5):
            ws.cell(row=r, column=cc).border = THIN
    for col, w in zip("ABCD", [2, 42, 20, 16]):
        ws.column_dimensions[col].width = w

    note_r = r + 2
    ws.cell(row=note_r, column=2,
            value="Gross market revenue from day-ahead arbitrage only, per year, not discounted.").font = Font(
        name=FONT, size=9, italic=True, color="C00000")

    # --- four charts in one row, calendar years on the x axis ---
    col = {c[0]: i + 1 for i, c in enumerate(ANNUAL_COLS)}
    years = Reference(ws_ann, min_col=col["calendar_year"], min_row=2, max_row=last)
    anchors = ["F4", "N4", "V4", "AD4"]

    ch1 = LineChart()
    ch1.add_data(Reference(ws_ann, min_col=col["revenue_k_eur_per_mw"], min_row=1, max_row=last),
                 titles_from_data=True)
    ch1.set_categories(years)
    _thin_line(ch1.series[0], CHART_COLORS[0])
    _style_chart(ch1, "Arbitrage revenue (k EUR/MW)", y_fmt="#,##0")
    ws.add_chart(ch1, anchors[0])

    ch2 = LineChart()
    for key in ["capacity_factor", "rte"]:
        ch2.add_data(Reference(ws_ann, min_col=col[key], min_row=1, max_row=last), titles_from_data=True)
    ch2.set_categories(years)
    for sr, color in zip(ch2.series, CHART_COLORS):
        _thin_line(sr, color)
    _style_chart(ch2, "Capacity and RTE", y_fmt="0%", legend=True)
    ch2.y_axis.scaling.min, ch2.y_axis.scaling.max, ch2.y_axis.majorUnit = 0.5, 1.0, 0.1
    ws.add_chart(ch2, anchors[1])

    ch3 = BarChart()
    ch3.type = "col"
    ch3.add_data(Reference(ws_ann, min_col=col["cycles"], min_row=1, max_row=last), titles_from_data=True)
    ch3.set_categories(years)
    _style_chart(ch3, "Equivalent cycles per year", y_fmt="#,##0")
    _bars(ch3, CHART_COLORS)
    ch3.y_axis.scaling.min = 0      # start at zero so small differences are not exaggerated
    ws.add_chart(ch3, anchors[2])

    ch4 = BarChart()
    ch4.type = "col"
    ch4.add_data(Reference(ws_ann, min_col=col["bought_k_eur"], max_col=col["sold_k_eur"], min_row=1,
                           max_row=last), titles_from_data=True)
    ch4.set_categories(years)
    _style_chart(ch4, "Bought and sold on the market (k EUR)", y_fmt="#,##0", legend=True)
    _bars(ch4, [CHART_COLORS[1], CHART_COLORS[2]])
    ch4.overlap = 100          # bought (negative) and sold (positive) on the same column
    ch4.x_axis.tickLblPos = "low"   # year labels below the negative bars
    ws.add_chart(ch4, anchors[3])
    ws.page_setup.orientation = "landscape"       # print the summary on one page
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1

    # ================= Raw_Data =================
    _write_raw_data(wb.create_sheet("Raw_Data"), summary["hourly_all"])

    # ================= Sample_Day =================
    if day_profile is not None and len(day_profile):
        wsd = wb.create_sheet("Sample_Day")
        wsd.sheet_view.showGridLines = False
        heads = ["Hour", "Price (EUR/MWh)", "Charge (MW)", "Discharge (MW)", "SOC (MWh)"]
        for j, h in enumerate(heads, start=1):
            wsd.cell(row=1, column=j, value=h)
        _style_header(wsd, 1, len(heads))
        for i, rec in enumerate(day_profile.itertuples(index=False), start=2):
            for j, v in enumerate(rec, start=1):
                c = wsd.cell(row=i, column=j, value=v)
                c.number_format = "#,##0.0##" if j > 1 else "@"
                c.font, c.border = BLACK, THIN
        lastd = 1 + len(day_profile)
        for col, w in zip("ABCDE", [10, 16, 14, 14, 14]):
            wsd.column_dimensions[col].width = w

        bar = BarChart()
        bar.type, bar.grouping, bar.overlap = "col", "stacked", 100
        for j, color in zip((3, 4), CHART_COLORS):
            bar.add_data(Reference(wsd, min_col=j, min_row=1, max_row=lastd), titles_from_data=True)
        bar.set_categories(Reference(wsd, min_col=1, min_row=2, max_row=lastd))

        line = LineChart()
        line.add_data(Reference(wsd, min_col=2, min_row=1, max_row=lastd), titles_from_data=True)
        line.y_axis.axId = 200
        _thin_line(line.series[0], "808080")
        _style_axis(line.y_axis, "EUR/MWh", "#,##0")
        line.y_axis.crosses = "max"
        _style_chart(bar, "Sample-day dispatch", y_title="MW", y_fmt="#,##0", legend=True,
                     height=10, width=24)
        bar.x_axis.tickLblSkip = 1
        bar.x_axis.tickLblPos = "low"
        _bars(bar, CHART_COLORS)
        bar += line
        wsd.add_chart(bar, "G2")

    wb.save(path)
    return path
