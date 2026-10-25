import re


_MISSING = {"NA", "N/A", "--", "-"}
_NUMBER = r"(-?(?:\d+(?:,\d{3})*(?:\.\d*)?|\.\d+))"


def _number(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group(1).strip()
            if value.upper() in _MISSING:
                return None
            try:
                return float(value.replace(",", ""))
            except ValueError:
                continue
    return None


def _text(text, pattern):
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


def _summary_columns(text):
    """Return the header/value pairs from the first Design Timing Summary row."""
    section = re.search(
        r"Design Timing Summary(.*?)(?:There are no user specified timing constraints|Clock Summary)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not section:
        return {}

    lines = section.group(1).splitlines()
    header_index = next(
        (i for i, line in enumerate(lines) if re.search(r"\bWNS\(ns\)", line, re.I)),
        None,
    )
    if header_index is None:
        return {}

    headers = [part.strip() for part in re.split(r"\s{2,}", lines[header_index].strip())]
    for line in lines[header_index + 1:]:
        if not re.search(r"\d|\bNA\b|N/A", line, re.I):
            continue
        values = re.findall(r"\bNA\b|--|-?\d+(?:,\d{3})*(?:\.\d+)?", line, re.I)
        if len(values) < len(headers):
            continue
        result = {}
        for header, value in zip(headers, values):
            if value.upper() in _MISSING:
                result[header.lower()] = None
            else:
                try:
                    result[header.lower()] = float(value.replace(",", ""))
                except ValueError:
                    result[header.lower()] = None
        return result
    return {}


def _summary_value(columns, label):
    return columns.get(label.lower())


def _clock_information(text):
    lines = text.splitlines()
    clocks = {}

    # Read clocks only from Clock Summary. Intra/Inter Clock tables contain
    # path groups and section headings that are not clock definitions.
    for section_index, line in enumerate(lines):
        if not re.match(r"^\s*\|?\s*Clock Summary\s*$", line, re.IGNORECASE):
            continue
        section_end = next(
            (i for i in range(section_index + 1, len(lines))
             if re.match(r"^\s*\|\s*[A-Za-z][^|]*$", lines[i])),
            len(lines),
        )
        section = lines[section_index + 1:section_end]
        header_index = None
        headers = []
        for index, candidate in enumerate(section):
            parts = [part.strip().lower() for part in re.split(r"\s{2,}", candidate.strip())]
            if parts and parts[0] in {"clock", "clock name"} and any(
                re.search(r"period\s*\(ns\)", part) for part in parts
            ):
                header_index, headers = index, parts
                break
        if header_index is None:
            continue
        period_index = next(
            i for i, header in enumerate(headers)
            if re.search(r"period\s*\(ns\)", header)
        )
        for row_line in section[header_index + 1:]:
            stripped = row_line.strip()
            if not stripped or re.fullmatch(r"[-\s]+", stripped):
                if clocks:
                    break
                continue
            cells = [part.strip() for part in re.split(r"\s{2,}", stripped)]
            if len(cells) < len(headers):
                continue
            name = cells[0]
            if not re.fullmatch(r"[A-Za-z0-9_./\[\]$:-]+", name):
                continue
            period = _number(cells[period_index], [rf"^{_NUMBER}$"])
            valid_period = period if period is not None and period > 0 else None
            if name not in clocks or (clocks[name] is None and valid_period is not None):
                clocks[name] = valid_period

    return clocks


def _coverage(text, columns, clocks):
    issue_counts = {
        name.lower(): int(count)
        for name, count in re.findall(
            r"^\s*(?:\d+\.\s*)?checking\s+([a-z0-9_]+)\s*\((\d+)\)",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
    }
    return {
        "clock_count": len(clocks),
        "clock_names": list(clocks),
        "clock_periods_ns": {name: period for name, period in clocks.items() if period is not None},
        "check_timing_checks_reported": len(issue_counts),
        "check_timing_issue_counts": issue_counts,
        "unconstrained_internal_endpoints": issue_counts.get("unconstrained_internal_endpoints"),
        "setup_total_endpoints": columns.get("tns total endpoints"),
        "hold_total_endpoints": columns.get("ths total endpoints"),
    }


def parse_timing_report(report_text: str) -> dict:
    text = report_text.replace("\r\n", "\n").replace("\r", "\n")
    columns = _summary_columns(text)

    no_constraints = bool(re.search(
        r"There are no user specified timing constraints", text, re.IGNORECASE
    ))
    has_summary_result = any(
        columns.get(key) is not None
        for key in ("wns(ns)", "tns(ns)", "whs(ns)", "ths(ns)", "wpws(ns)", "tpws(ns)")
    )

    # A detailed report_timing report has one path rather than summary slack
    # columns. Only parse values explicitly labeled by Vivado.
    slack_ns = _number(text, [rf"^\s*Slack\s*\([^)]*\)\s*:\s*{_NUMBER}\s*ns\b"])
    data_path_delay_ns = _number(text, [rf"^\s*Data Path Delay\s*:\s*{_NUMBER}\s*ns\b"])
    data_arrival_time_ns = _number(text, [
        rf"^\s*Data Arrival Time\s+{_NUMBER}",
        rf"^\s*arrival time\s+{_NUMBER}",
    ])
    data_required_time_ns = _number(text, [
        rf"^\s*Data Required Time\s+{_NUMBER}",
        rf"^\s*required time\s+{_NUMBER}",
    ])

    detailed_path = bool(re.search(r"^\s*Slack\s*\([^)]*\)\s*:", text, re.I | re.M))
    constraints_detected = False if no_constraints else (
        True if has_summary_result or detailed_path else None
    )
    clocks = _clock_information(text)

    return {
        "report_type": "timing",
        "wns_ns": _summary_value(columns, "WNS(ns)"),
        "tns_ns": _summary_value(columns, "TNS(ns)"),
        "whs_ns": _summary_value(columns, "WHS(ns)"),
        "ths_ns": _summary_value(columns, "THS(ns)"),
        "wpws_ns": _summary_value(columns, "WPWS(ns)"),
        "tpws_ns": _summary_value(columns, "TPWS(ns)"),
        "tns_failing_endpoints": _summary_value(columns, "TNS Failing Endpoints"),
        "tns_total_endpoints": _summary_value(columns, "TNS Total Endpoints"),
        "ths_failing_endpoints": _summary_value(columns, "THS Failing Endpoints"),
        "ths_total_endpoints": _summary_value(columns, "THS Total Endpoints"),
        "startpoint": _text(text, r"^\s*Startpoint:\s*(.+)$") or _text(text, r"^\s*Source:\s*(.+)$"),
        "endpoint": _text(text, r"^\s*Endpoint:\s*(.+)$") or _text(text, r"^\s*Destination:\s*(.+)$"),
        "data_arrival_time_ns": data_arrival_time_ns,
        "data_required_time_ns": data_required_time_ns,
        "slack_ns": slack_ns,
        "data_path_delay_ns": data_path_delay_ns,
        "logic_levels": _number(text, [r"^\s*Logic Levels:\s*(\d+)\b"]),
        "path_group": _text(text, r"^\s*Path Group:\s*(.+)$"),
        "path_type": _text(text, r"^\s*Path Type:\s*(.+)$"),
        "timing_constraints_detected": constraints_detected,
        "clock_names": list(clocks),
        "clock_periods_ns": {name: period for name, period in clocks.items() if period is not None},
        "timing_analysis_coverage": _coverage(text, columns, clocks),
        "timing_status": "unconstrained" if no_constraints else (
            "timing_reported" if has_summary_result or detailed_path else "needs_validation"
        ),
    }
