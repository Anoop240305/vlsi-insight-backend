import json
import sys
from pathlib import Path

from vivado_report_parser import parse_report


def main():
    if len(sys.argv) != 2:
        print("Usage: python test_parser.py path/to/report.rpt")
        raise SystemExit(1)

    report_path = Path(sys.argv[1])

    if not report_path.is_file():
        print(f"File not found: {report_path}")
        raise SystemExit(1)

    report_text = report_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    try:
        result = parse_report(report_text)
    except ValueError as exc:
        print(f"Parsing failed: {exc}")
        raise SystemExit(1)

    # Keep utilization CLI output focused on the requested resource metrics.
    # parse_report attaches metadata for application/API consumers, but it is
    # not part of the utilization parser's public metric payload.
    if result.get("generating_command") == "report_utilization":
        result = {
            key: value
            for key, value in result.items()
            if key not in {"metadata", "generating_command", "report_type"}
        }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
