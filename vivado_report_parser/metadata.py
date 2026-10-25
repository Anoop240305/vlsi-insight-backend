
import re


def extract_metadata(report_text: str) -> dict:
    metadata = {}

    for line in report_text.splitlines():
        match = re.match(
            r"^\s*\|?\s*([^:|]+?)\s*:\s*(.*?)\s*$",
            line
        )

        if match:
            key = match.group(1).strip().lower()
            value = match.group(2).strip()

            if key in {
                "command",
                "design",
                "device",
                "tool version",
                "date",
                "host",
                "design state",
                "speed file",
            }:
                metadata[key] = value

    return metadata


def get_generating_command(report_text: str) -> str | None:
    metadata = extract_metadata(report_text)
    command = metadata.get("command")

    if not command:
        return None

    return command.split()[0].lower()
