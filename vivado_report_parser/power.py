import re


_MISSING = {"", "NA", "N/A", "--", "---", "UNSPECIFIED*"}


def _table_rows(text):
    """Read Vivado's pipe-delimited tables without relying on column widths."""
    tables = []
    current = []
    for line in text.splitlines():
        if re.fullmatch(r"\s*\+[-+]+\+\s*", line):
            continue
        if "|" not in line:
            if current:
                tables.append(current)
                current = []
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r"[-+\s]+", cell or " ") for cell in cells):
            continue
        current.append(cells)
    if current:
        tables.append(current)
    return tables


def _parse_number(value):
    value = value.strip().replace(",", "")
    if value.upper() in _MISSING:
        return None
    match = re.fullmatch(r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:W|A|V)?", value, re.I)
    return float(match.group(1)) if match else None


def _summary_values(tables):
    for rows in tables:
        if not rows or "Total On-Chip Power (W)" not in rows[0]:
            continue
        values = {}
        for row in rows:
            if len(row) >= 2:
                values[row[0].strip().lower()] = row[1].strip()
        return values
    return {}


def _component_values(tables):
    for rows in tables:
        if not rows or "On-Chip" not in rows[0] or "Power (W)" not in rows[0]:
            continue
        headers = [cell.strip().lower() for cell in rows[0]]
        result = {}
        for row in rows[1:]:
            if len(row) != len(headers):
                continue
            label = row[0].strip().lower()
            try:
                power_index = headers.index("power (w)")
            except ValueError:
                continue
            result[label] = _parse_number(row[power_index])
        return result
    return {}


def _metric(summary, key):
    return _parse_number(summary.get(key.lower(), ""))


def parse_power_report(report_text: str) -> dict:
    text = report_text.replace("\r\n", "\n").replace("\r", "\n")
    tables = _table_rows(text)
    summary = _summary_values(tables)
    components = _component_values(tables)

    return {
        "report_type": "power",
        "total_on_chip_power_w": _metric(summary, "Total On-Chip Power (W)"),
        "design_power_budget_w": _metric(summary, "Design Power Budget (W)"),
        "dynamic_power_w": _metric(summary, "Dynamic (W)"),
        "static_power_w": _metric(summary, "Device Static (W)"),
        "logic_power_w": components.get("slice logic"),
        "signal_power_w": components.get("signals"),
        "clock_power_w": components.get("clocks"),
        "io_power_w": components.get("i/o"),
        "confidence": summary.get("confidence level", "").title() or None,
    }
