import unittest
from pathlib import Path

from vivado_report_parser import parse_report


class PowerParserTests(unittest.TestCase):
    def test_vivado_power_report_extracts_summary_and_components(self):
        report = (Path(__file__).resolve().parent / "my_power_report.rpt").read_text(
            encoding="utf-8", errors="replace"
        )
        parsed = parse_report(report)

        self.assertEqual(parsed["generating_command"], "report_power")
        self.assertEqual(parsed["total_on_chip_power_w"], 0.747)
        self.assertEqual(parsed["dynamic_power_w"], 0.663)
        self.assertEqual(parsed["static_power_w"], 0.084)
        self.assertEqual(parsed["logic_power_w"], 0.005)
        self.assertEqual(parsed["signal_power_w"], 0.012)
        self.assertEqual(parsed["io_power_w"], 0.646)
        self.assertIsNone(parsed["clock_power_w"])
        self.assertEqual(parsed["confidence"], "Low")
        self.assertAlmostEqual(
            parsed["logic_power_w"] + parsed["signal_power_w"] + parsed["io_power_w"],
            parsed["dynamic_power_w"],
        )


if __name__ == "__main__":
    unittest.main()
