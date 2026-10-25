from .metadata import extract_metadata, get_generating_command
from .timing import parse_timing_report
from .power import parse_power_report
from .utilization import parse_utilization_report


def parse_report(report_text: str) -> dict:
    command = get_generating_command(report_text)
    metadata = extract_metadata(report_text)

    if command in {"report_timing", "report_timing_summary"}:
        parsed = parse_timing_report(report_text)

    elif command == "report_power":
        parsed = parse_power_report(report_text)

    elif command == "report_utilization":
        parsed = parse_utilization_report(report_text)

    else:
        raise ValueError(
            f"Unsupported or missing report command: {command!r}"
        )

    parsed["metadata"] = metadata
    parsed["generating_command"] = command

    return parsed