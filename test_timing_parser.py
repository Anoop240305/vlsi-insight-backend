import unittest
from pathlib import Path

from vivado_report_parser import parse_report


ROOT = Path(__file__).resolve().parent


class TimingParserTests(unittest.TestCase):
    def test_unconstrained_summary_keeps_na_as_none(self):
        report = (ROOT / "my_timing_report alu.rpt").read_text(
            encoding="utf-8", errors="replace"
        )
        parsed = parse_report(report)

        self.assertEqual(parsed["generating_command"], "report_timing_summary")
        self.assertIsNone(parsed["wns_ns"])
        self.assertIsNone(parsed["tns_ns"])
        self.assertIsNone(parsed["slack_ns"])
        self.assertFalse(parsed["timing_constraints_detected"])
        self.assertEqual(parsed["timing_status"], "unconstrained")
        self.assertEqual(parsed["metadata"]["design"], "alu")

    def test_detailed_report_timing_extracts_reported_path(self):
        report = (ROOT / "timing.rpt").read_text(
            encoding="utf-8", errors="replace"
        )
        parsed = parse_report(report)

        self.assertEqual(parsed["generating_command"], "report_timing")
        self.assertEqual(parsed["slack_ns"], 6.995)
        self.assertEqual(parsed["startpoint"], "in0_V[1]")
        self.assertEqual(parsed["endpoint"], "out_V[8]")
        self.assertEqual(parsed["data_path_delay_ns"], 3.005)
        self.assertEqual(parsed["data_required_time_ns"], 10.0)
        self.assertTrue(parsed["timing_constraints_detected"])

    def test_summary_row_maps_numeric_values_to_their_labels(self):
        report = """Design Timing Summary
WNS(ns)  TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints
------   -------  ---------------------  -------------------
-0.250   -1.500   2                      12
Clock Summary
"""
        parsed = parse_report(
            "| Command : report_timing_summary -file report.rpt\n" + report
        )

        self.assertEqual(parsed["wns_ns"], -0.25)
        self.assertEqual(parsed["tns_ns"], -1.5)
        self.assertEqual(parsed["tns_failing_endpoints"], 2.0)
        self.assertEqual(parsed["tns_total_endpoints"], 12.0)

    def test_clock_periods_and_check_timing_coverage_are_extracted(self):
        report = """| Command : report_timing_summary -file timing.rpt
check_timing report
1. checking no_clock (0)
4. checking unconstrained_internal_endpoints (0)
Design Timing Summary
WNS(ns)  TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints
------   -------  ---------------------  -------------------
0.125    0.000    0                      4
Clock Summary
Clock        Waveform(ns)       Period(ns)  Frequency(MHz)
clk_core     {0.000 5.000}      10.000      100.000
Intra Clock Table
"""
        parsed = parse_report(report)
        self.assertEqual(parsed["clock_names"], ["clk_core"])
        self.assertEqual(parsed["clock_periods_ns"]["clk_core"], 10.0)
        checks = parsed["timing_analysis_coverage"]["check_timing_issue_counts"]
        self.assertEqual(checks["no_clock"], 0)
        self.assertEqual(checks["unconstrained_internal_endpoints"], 0)

    def test_clock_summary_excludes_following_table_headings(self):
        report = (ROOT / "alu_timing_report.rpt").read_text(encoding="utf-8", errors="replace")
        parsed = parse_report(report)
        self.assertEqual(parsed["clock_names"], ["clk"])
        self.assertEqual(parsed["timing_analysis_coverage"]["clock_count"], 1)
        self.assertEqual(parsed["clock_periods_ns"], {"clk": 10.0})

    def test_multiple_clock_names_are_read_only_from_clock_summary(self):
        report = """| Command : report_timing_summary -file clocks.rpt
| Clock Summary
Clock        Waveform(ns)       Period(ns)
clk_a        {0.000 2.500}      5.000
clk_b        {0.000 5.000}      10.000

| Intra Clock Table
Clock    WNS(ns)    TNS(ns)
clk_a    0.1        0.0
| Inter Clock Table
From Clock    To Clock
"""
        parsed = parse_report(report)
        self.assertEqual(parsed["clock_names"], ["clk_a", "clk_b"])
        self.assertEqual(parsed["timing_analysis_coverage"]["clock_count"], 2)

    def test_clock_name_without_period_does_not_infer_frequency(self):
        report = """| Command : report_timing_summary -file no_period.rpt
| Clock Summary
Clock        Waveform(ns)       Period(ns)
clk_unknown  NA                 NA
| Intra Clock Table
Clock    WNS(ns)
section heading is not a clock
"""
        parsed = parse_report(report)
        self.assertEqual(parsed["clock_names"], ["clk_unknown"])
        self.assertEqual(parsed["timing_analysis_coverage"]["clock_count"], 1)
        self.assertEqual(parsed["clock_periods_ns"], {})


if __name__ == "__main__":
    unittest.main()
