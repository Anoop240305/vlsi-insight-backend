import unittest
from pathlib import Path

from vivado_report_parser import parse_report


class UtilizationParserTests(unittest.TestCase):
    def test_vivado_utilization_tables_map_used_available_and_percent(self):
        report = (Path(__file__).resolve().parent / "my_utilization_report alu.rpt").read_text(
            encoding="utf-8", errors="replace"
        )
        parsed = parse_report(report)

        self.assertEqual(parsed["generating_command"], "report_utilization")
        self.assertEqual(parsed["slice_luts"]["used"], 1)
        self.assertEqual(parsed["slice_luts"]["available"], 17600)
        self.assertEqual(parsed["slice_luts"]["utilization_percent"], "<0.01")
        self.assertAlmostEqual(parsed["slice_luts"]["utilization_percent_numeric"], 100 / 17600)
        self.assertEqual(parsed["slice_registers"]["used"], 0)
        self.assertEqual(parsed["slice_registers"]["available"], 35200)
        self.assertEqual(parsed["block_ram_tiles"]["used"], 0)
        self.assertEqual(parsed["block_ram_tiles"]["available"], 60)
        self.assertEqual(parsed["dsp_blocks"]["used"], 0)
        self.assertEqual(parsed["dsp_blocks"]["available"], 80)
        self.assertEqual(parsed["bonded_iobs"]["used"], 17)
        self.assertEqual(parsed["bonded_iobs"]["available"], 54)
        self.assertEqual(parsed["lut_as_logic_used"], 1)
        self.assertEqual(parsed["lut_as_memory_used"], 0)
        self.assertEqual(parsed["clock_buffers_bufgctrl_used"], 0)
        self.assertIsNone(parsed["slice_resources_used"])


if __name__ == "__main__":
    unittest.main()
