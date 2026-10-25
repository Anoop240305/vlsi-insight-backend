import re


_MISSING = {"", "NA", "N/A", "--", "---"}


def _read_tables(text):
    """Split Vivado's ASCII tables into rows of pipe-delimited cells."""
    tables = []
    current = []

    for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        # Vivado uses +----+ borders, which should not split a table.
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


def _count(value):
    value = value.strip().replace(",", "")
    if value.upper() in _MISSING or not re.fullmatch(r"\d+", value):
        return None
    return int(value)


def _percent_display(value):
    value = value.strip()
    if value.upper() in _MISSING:
        return None
    # Keep Vivado's displayed text (for example, "<0.01") as-is.
    if re.fullmatch(r"<\s*\d+(?:\.\d+)?%?", value) or re.fullmatch(r"\d+(?:\.\d+)?%?", value):
        return value
    return None


def _percent_numeric(display, used, available):
    if used is not None and available not in (None, 0):
        return used * 100.0 / available
    if display is None:
        return None
    match = re.fullmatch(r"\s*(<)?\s*(\d+(?:\.\d+)?)\s*%?\s*", display)
    if not match:
        return None
    value = float(match.group(2))
    return f"<{value:g}" if match.group(1) else value


def _norm(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _resource_rows(tables):
    """Collect only rows from tables with a Used column, keyed by normalized name."""
    found = {}
    for rows in tables:
        if not rows:
            continue
        headers = [_norm(cell) for cell in rows[0]]
        name_index = next((i for i, header in enumerate(headers) if header in {"sitetype", "refname"}), None)
        used_index = next((i for i, header in enumerate(headers) if header == "used"), None)
        if name_index is None or used_index is None:
            continue
        available_index = next((i for i, header in enumerate(headers) if header == "available"), None)
        percent_index = next((i for i, header in enumerate(headers) if header in {"util", "utilization"}), None)

        for row in rows[1:]:
            if len(row) != len(headers):
                continue
            name = _norm(row[name_index])
            if not name:
                continue
            used = _count(row[used_index])
            available = _count(row[available_index]) if available_index is not None else None
            display = _percent_display(row[percent_index]) if percent_index is not None else None
            found[name] = {
                "used": used,
                "available": available,
                "display": display,
                "numeric": _percent_numeric(display, used, available),
            }
    return found


def parse_utilization_report(report_text: str) -> dict:
    resources = _resource_rows(_read_tables(report_text))

    def pick(*names):
        for name in names:
            if _norm(name) in resources:
                return resources[_norm(name)]
        return {"used": None, "available": None, "display": None, "numeric": None}

    def capacity(name, *aliases):
        resource = pick(name, *aliases)
        return {
            "used": resource["used"],
            "available": resource["available"],
            "utilization_percent": resource["display"],
            "utilization_percent_numeric": resource["numeric"],
        }

    return {
        "slice_luts": capacity("Slice LUTs*", "Slice LUTs"),
        "slice_registers": capacity("Slice Registers", "Slice Register", "Flip-Flops"),
        "block_ram_tiles": capacity("Block RAM Tile", "Block RAM Tiles"),
        "dsp_blocks": capacity("DSPs", "DSP", "DSP Blocks"),
        "bonded_iobs": capacity("Bonded IOB", "Bonded IOBs"),
        "lut_as_logic_used": pick("LUT as Logic")["used"],
        "lut_as_memory_used": pick("LUT as Memory")["used"],
        "register_as_flip_flop_used": pick("Register as Flip Flop", "Register as Flip-Flop")["used"],
        "register_as_latch_used": pick("Register as Latch")["used"],
        "f7_muxes_used": pick("F7 Muxes")["used"],
        "f8_muxes_used": pick("F8 Muxes")["used"],
        "clock_buffers_bufgctrl_used": pick("BUFGCTRL")["used"],
        "slice_resources_used": pick("Slices")["used"],
    }
