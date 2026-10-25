import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Report
from .health import evaluate_design_health, interpret_report


@override_settings(MEDIA_ROOT="")
class ReportApiTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_media.cleanup)
        self.settings_context = override_settings(MEDIA_ROOT=self.temp_media.name)
        self.settings_context.enable()
        self.addCleanup(self.settings_context.disable)

        self.user = get_user_model().objects.create_user(
            username="parser-user", password="test-password-123"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.root = Path(__file__).resolve().parent.parent

    def report_file(self, filename):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(
            filename,
            (self.root / filename).read_bytes(),
            content_type="application/octet-stream",
        )

    def test_health_is_public(self):
        response = APIClient().get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_upload_single_report_persists_metrics_and_dashboard_data(self):
        response = self.client.post(
            "/api/reports/upload/",
            {"files": [self.report_file("my_power_report.rpt")]},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()["reports"][0]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["report_type"], "power")
        self.assertNotIn("metadata", result["metrics"])
        self.assertTrue(result["interpretations"])

        record = Report.objects.get(pk=result["id"])
        self.assertEqual(record.parsed_metrics["total_on_chip_power_w"], 0.747)
        self.assertEqual(record.source_metadata["design"], "half_adder")
        self.assertEqual(record.stage, Report.Stage.ROUTE_DESIGN)
        self.assertEqual(record.device_part, "xc7z010iclg225-1L")
        normalized = record.normalized_metrics["total_on_chip_power_w"]
        self.assertEqual(normalized["value"], 0.747)
        self.assertEqual(normalized["unit"], "W")
        self.assertEqual(normalized["source_report_id"], record.pk)
        self.assertEqual(normalized["extraction_status"], "extracted")

        dashboard = self.client.get("/api/dashboard/").json()
        self.assertEqual(dashboard["project"]["name"], "half_adder")
        self.assertEqual(
            dashboard["latest"]["power"]["metrics"]["total_on_chip_power_w"],
            0.747,
        )

    def test_three_report_types_are_parsed_independently(self):
        response = self.client.post(
            "/api/reports/upload/",
            {"files": [
                self.report_file("my_timing_report alu.rpt"),
                self.report_file("my_power_report.rpt"),
                self.report_file("my_utilization_report alu.rpt"),
            ]},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()["reports"]
        self.assertEqual([item["status"] for item in results], ["completed"] * 3)
        self.assertEqual(
            {item["report_type"] for item in results},
            {"timing", "power", "utilization"},
        )
        self.assertEqual(len({item["run_id"] for item in results}), 1)
        utilization = next(item for item in results if item["report_type"] == "utilization")
        self.assertEqual(utilization["metrics"]["slice_luts"]["used"], 1)
        self.assertNotIn("metadata", utilization["metrics"])

    def test_previous_upload_versions_are_retained_for_comparison(self):
        first = self.client.post(
            "/api/reports/upload/",
            {"files": [self.report_file("my_power_report.rpt")]},
            format="multipart",
        ).json()["reports"][0]
        second = self.client.post(
            "/api/reports/upload/",
            {"files": [self.report_file("my_power_report.rpt")]},
            format="multipart",
        ).json()["reports"][0]

        dashboard = self.client.get("/api/dashboard/").json()
        self.assertEqual(len(dashboard["reports"]), 2)
        self.assertEqual(dashboard["latest"]["power"]["id"], second["id"])
        self.assertNotEqual(first["id"], second["id"])
        project_id = Report.objects.get(pk=first["id"]).project_id
        older_run = self.client.get(
            f"/api/dashboard/?project_id={project_id}&run_id={first['run_id']}"
        ).json()
        self.assertEqual(older_run["active_run_id"], first["run_id"])
        self.assertEqual(older_run["latest"]["power"]["id"], first["id"])
        self.assertEqual(len(older_run["reports"]), 2)

    def test_upload_can_target_selected_project_when_source_design_differs(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from projects.models import Project

        initial = self.client.post(
            "/api/reports/upload/", {"files": [self.report_file("my_power_report.rpt")]}, format="multipart"
        ).json()["reports"][0]
        selected_project_id = initial["project_id"]
        source = (self.root / "my_power_report.rpt").read_bytes().replace(b"| Design           : half_adder", b"| Design           : bd_0_wrapper")
        response = self.client.post(
            "/api/reports/upload/",
            {"project_id": str(selected_project_id), "files": [SimpleUploadedFile("power_new.rpt", source)]},
            format="multipart",
        )
        result = response.json()["reports"][0]
        self.assertEqual(result["project_id"], selected_project_id)
        self.assertEqual(result["source_design"], "bd_0_wrapper")
        self.assertEqual(Project.objects.filter(owner=self.user).count(), 1)
        self.assertEqual(len(self.client.get(f"/api/dashboard/?project_id={selected_project_id}").json()["reports"]), 2)

    def test_dashboard_query_never_combines_different_designs(self):
        timing = self.client.post(
            "/api/reports/upload/",
            {"files": [self.report_file("my_timing_report alu.rpt")]},
            format="multipart",
        ).json()["reports"][0]
        power = self.client.post(
            "/api/reports/upload/",
            {"files": [self.report_file("my_power_report.rpt")]},
            format="multipart",
        ).json()["reports"][0]
        timing_project = Report.objects.get(pk=timing["id"]).project_id
        power_project = Report.objects.get(pk=power["id"]).project_id
        self.assertNotEqual(timing_project, power_project)

        timing_dashboard = self.client.get(f"/api/dashboard/?project_id={timing_project}").json()
        power_dashboard = self.client.get(f"/api/dashboard/?project_id={power_project}").json()
        self.assertEqual([report["report_type"] for report in timing_dashboard["reports"]], ["timing"])
        self.assertEqual([report["report_type"] for report in power_dashboard["reports"]], ["power"])

    def test_invalid_and_unrecognized_reports_are_failed(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(
            "/api/reports/upload/",
            {"files": [
                SimpleUploadedFile("notes.txt", b"not an rpt"),
                SimpleUploadedFile("unknown.rpt", b"no Vivado command here"),
            ]},
            format="multipart",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["status"] for r in response.json()["reports"]], ["failed", "failed"])
        self.assertEqual(Report.objects.filter(status=Report.Status.COMPLETED).count(), 0)

    def test_timing_fixture_upload_uses_the_same_parser_as_the_console(self):
        response = self.client.post(
            "/api/reports/upload/",
            {"files": [self.report_file("alu_timing_report.rpt")]},
            format="multipart",
        )
        result = response.json()["reports"][0]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["report_type"], "timing")
        self.assertEqual(result["metrics"]["wns_ns"], 0.85)
        self.assertEqual(Report.objects.filter(status=Report.Status.COMPLETED).count(), 1)

    def test_negative_wns_report_produces_timing_failure_and_finding(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        report_text = """| Command : report_timing_summary -file negative.rpt
| Design : negative_design
| Device : xc7z010
| Design State : Routed
check_timing report
1. checking no_clock (0)
4. checking unconstrained_internal_endpoints (0)
5. checking no_input_delay (0)
6. checking no_output_delay (0)
Design Timing Summary
WNS(ns)  TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints
------   -------  ---------------------  -------------------
-0.125   -0.800   2                      16
Clock Summary
"""
        response = self.client.post(
            "/api/reports/upload/",
            {"files": [SimpleUploadedFile("negative.rpt", report_text.encode())]},
            format="multipart",
        )
        result = response.json()["reports"][0]
        self.assertEqual(result["status"], "completed")
        dashboard = self.client.get("/api/dashboard/").json()
        self.assertEqual(dashboard["analysis_coverage"]["timing_readiness"]["state"], "ANALYZED_FAIL")
        finding = next(item for item in dashboard["design_health"]["findings"] if item["code"] == "NEGATIVE_SLACK")
        self.assertEqual(finding["measured_evidence"]["value"], -0.125)

    def test_matching_combinational_run_does_not_require_a_clock(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        timing_text = b"""| Command : report_timing_summary -file no_clock.rpt
| Design : combinational_block
| Device : xc7z010iclg225-1L
| Design State : Routed
check_timing report
1. checking no_clock (0)
4. checking unconstrained_internal_endpoints (0)
There are no user specified timing constraints.
Design Timing Summary
"""
        utilization_text = (
            (self.root / "my_utilization_report alu.rpt").read_bytes()
            .replace(b"| Design       : alu", b"| Design       : combinational_block")
            .replace(b"| Design State : Synthesized", b"| Design State : Routed")
        )
        response = self.client.post(
            "/api/reports/upload/",
            {"files": [
                SimpleUploadedFile("no_clock.rpt", timing_text),
                SimpleUploadedFile("no_clock_util.rpt", utilization_text),
            ]},
            format="multipart",
        )
        self.assertEqual([item["status"] for item in response.json()["reports"]], ["completed", "completed"])
        dashboard = self.client.get("/api/dashboard/").json()
        self.assertEqual(
            dashboard["analysis_coverage"]["timing_readiness"]["state"],
            "NOT_APPLICABLE",
            dashboard["analysis_coverage"],
        )

    def test_batch_over_three_is_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        files = [SimpleUploadedFile(f"{i}.rpt", b"x") for i in range(4)]
        response = self.client.post(
            "/api/reports/upload/", {"files": files}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Report.objects.count(), 0)

    def test_upload_requires_authentication(self):
        response = APIClient().post("/api/reports/upload/", {}, format="multipart")
        self.assertEqual(response.status_code, 401)

    def test_clock_serializer_filters_legacy_section_headings_and_recounts(self):
        from .api_views import _clean_clock_fields

        cleaned = _clean_clock_fields({
            "clock_count": 6,
            "clock_names": ["clk", "| Inter Clock Table", "From Clock", "clk"],
            "clock_periods_ns": {"clk": 10.0, "From Clock": 20.0},
        })
        self.assertEqual(cleaned["clock_names"], ["clk"])
        self.assertEqual(cleaned["clock_count"], 1)
        self.assertEqual(cleaned["clock_periods_ns"], {"clk": 10.0})

    def test_token_login_authenticates_dashboard_requests(self):
        client = APIClient()
        login = client.post(
            "/api/token/",
            {"username": "parser-user", "password": "test-password-123"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertIn("token", login.json())
        client.credentials(HTTP_AUTHORIZATION=f"Token {login.json()['token']}")
        self.assertEqual(client.get("/api/dashboard/").status_code, 200)

    def test_oversized_file_is_rejected_without_persistence(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(
            "/api/reports/upload/",
            {"files": [SimpleUploadedFile("oversized.rpt", b"x" * (10 * 1024 * 1024 + 1))]},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reports"][0]["status"], "failed")
        self.assertEqual(Report.objects.count(), 0)


class TimingHealthEngineTests(TestCase):
    def timing_report(self, metrics, coverage=None):
        return SimpleNamespace(
            pk=17,
            name="timing.rpt",
            stage="route_design",
            run_id="run-1",
            parsed_metrics=metrics,
            analysis_coverage=coverage or {"check_timing_issue_counts": {}},
            source_metadata={"command": "report_timing_summary -file timing.rpt"},
        )

    def test_no_constraints_is_unknown_not_pass(self):
        timing = self.timing_report({"timing_constraints_detected": False})
        health, readiness = evaluate_design_health({"timing": timing})
        self.assertEqual(readiness["state"], "MISSING_CONSTRAINTS")
        self.assertIsNone(health["score"])
        self.assertEqual(health["assessment"], "Provisional")
        self.assertTrue(any(f["code"] == "TIMING_CONSTRAINTS_MISSING" for f in health["findings"]))

    def test_negative_wns_creates_evidence_based_timing_finding(self):
        timing = self.timing_report({
            "timing_constraints_detected": True,
            "wns_ns": -0.125,
            "tns_ns": -0.8,
            "whs_ns": 0.1,
            "ths_ns": 0.0,
        })
        health, readiness = evaluate_design_health({"timing": timing})
        self.assertEqual(readiness["state"], "ANALYZED_FAIL")
        finding = next(f for f in health["findings"] if f["code"] == "NEGATIVE_SLACK")
        self.assertEqual(finding["measured_evidence"]["value"], -0.125)
        self.assertEqual(finding["measured_evidence"]["unit"], "ns")

    def test_interpretation_rules_cover_positive_zero_negative_and_missing_slack(self):
        report = self.timing_report({"wns_ns": 0.25, "tns_ns": 0.0, "whs_ns": -0.02, "ths_ns": 0.0})
        report.report_type = "timing"
        findings = interpret_report(report)
        setup = next(item for item in findings if item["title"] == "Setup slack")
        hold = next(item for item in findings if item["title"] == "Hold slack")
        self.assertEqual(setup["severity"], "informational")
        self.assertEqual(hold["severity"], "critical")
        self.assertEqual(setup["observed_value"]["tns_ns"], 0.0)

        report.parsed_metrics = {}
        missing = interpret_report(report)
        self.assertIsNone(missing[0]["observed_value"])
        self.assertIn("No usable timing slack", missing[0]["engineering_interpretation"])

    def test_interpretation_keeps_zero_tns_distinct_from_negative_tns_and_hold(self):
        report = self.timing_report({"wns_ns": 0.0, "tns_ns": -0.5, "whs_ns": 0.1, "ths_ns": -0.01})
        report.report_type = "timing"
        findings = interpret_report(report)
        setup = next(item for item in findings if item["title"] == "Setup slack")
        hold = next(item for item in findings if item["title"] == "Hold slack")
        self.assertEqual(setup["severity"], "critical")
        self.assertEqual(setup["observed_value"]["wns_ns"], 0.0)
        self.assertEqual(setup["observed_value"]["tns_ns"], -0.5)
        self.assertEqual(hold["severity"], "critical")
        self.assertEqual(hold["observed_value"]["whs_ns"], 0.1)
        self.assertEqual(hold["observed_value"]["ths_ns"], -0.01)

    def test_interpretation_consumes_normalized_metric_values(self):
        report = self.timing_report({"wns_ns": -9.0})
        report.report_type = "timing"
        report.normalized_metrics = {
            "wns_ns": {"value": 0.125, "unit": "ns"},
            "tns_ns": {"value": 0.0, "unit": "ns"},
        }
        finding = next(item for item in interpret_report(report) if item["title"] == "Setup slack")
        self.assertEqual(finding["observed_value"]["wns_ns"], 0.125)

    def test_utilization_interpretation_requires_valid_capacity(self):
        report = SimpleNamespace(
            report_type="utilization", name="util.rpt", stage="route_design",
            parsed_metrics={"slice_luts": {"used": 90, "available": 100}, "dsp_blocks": {"used": 2, "available": None}},
        )
        findings = interpret_report(report)
        self.assertEqual(findings[0]["observed_value"]["utilization_percent"], 90.0)
        self.assertNotIn("dsp_blocks", str(findings))

    def test_power_interpretation_reports_largest_component_without_adding_to_total(self):
        report = SimpleNamespace(
            report_type="power", name="power.rpt", stage="route_design",
            parsed_metrics={"total_on_chip_power_w": 1.0, "dynamic_power_w": 0.8, "static_power_w": 0.2,
                            "logic_power_w": 0.1, "signal_power_w": 0.2, "io_power_w": 0.5, "confidence": "Low"},
        )
        findings = interpret_report(report)
        component = next(item for item in findings if item["title"].startswith("Largest reported component"))
        self.assertEqual(component["observed_metric"], "I/O")
        self.assertEqual(component["observed_value"], 0.5)
        power_total = next(item for item in findings if item["title"] == "Power estimate")
        self.assertEqual(power_total["observed_value"]["dynamic_power_w"], 0.8)
        self.assertEqual(power_total["observed_value"]["total_on_chip_power_w"], 1.0)

    def test_report_timing_single_positive_path_is_not_design_wide_pass(self):
        timing = self.timing_report({"timing_constraints_detected": True, "slack_ns": 0.25})
        timing.source_metadata["command"] = "report_timing -file path.rpt"
        _, readiness = evaluate_design_health({"timing": timing})
        self.assertEqual(readiness["state"], "INCOMPLETE_ANALYSIS")
