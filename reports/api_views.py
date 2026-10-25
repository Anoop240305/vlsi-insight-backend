from pathlib import Path
import re
from uuid import uuid4

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.text import get_valid_filename
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from projects.models import Project
from vivado_report_parser import parse_report
from vivado_report_parser.metadata import extract_metadata
from .models import Report
from .health import evaluate_design_health, interpret_report


MAX_REPORTS_PER_REQUEST = 3
MAX_REPORT_SIZE = 10 * 1024 * 1024
SUPPORTED_TYPES = {"timing", "power", "utilization"}
_CLOCK_NAME = re.compile(r"^[A-Za-z0-9_./\[\]$:-]+$")


def _clean_clock_fields(value):
    if not isinstance(value, dict):
        return value
    cleaned = dict(value)
    names = value.get("clock_names")
    if isinstance(names, list):
        valid_names = list(dict.fromkeys(
            name for name in names
            if isinstance(name, str) and _CLOCK_NAME.fullmatch(name.strip())
        ))
        cleaned["clock_names"] = valid_names
        cleaned["clock_count"] = len(valid_names)
        periods = value.get("clock_periods_ns")
        if isinstance(periods, dict):
            cleaned["clock_periods_ns"] = {
                name: period for name, period in periods.items()
                if name in valid_names and isinstance(period, (int, float)) and period > 0
            }
    return cleaned


def _safe_project_name(name):
    cleaned = get_valid_filename(str(name or "").strip())[:190]
    return cleaned or "unidentified-design"


def _project_for(user, design_name):
    project, _ = Project.objects.get_or_create(
        owner=user,
        name=_safe_project_name(design_name),
        defaults={"description": "Created from uploaded Vivado reports."},
    )
    return project


def _stage_for(metadata):
    state = str(metadata.get("design state", "")).lower()
    if "synth" in state:
        return Report.Stage.SYNTHESIS
    if "route" in state:
        return Report.Stage.ROUTE_DESIGN
    if "implement" in state or "place" in state:
        return Report.Stage.IMPLEMENTATION
    return Report.Stage.UNKNOWN


def _metric_unit(path):
    key = path.lower()
    if "_ns" in key or key.endswith(".period"):
        return "ns"
    if "_w" in key:
        return "W"
    if "percent" in key or key.endswith("_pct"):
        return "%"
    if any(part in key for part in ("_used", "_available", "_endpoints", "_count", "_levels", ".used", ".available")):
        return "count"
    return None


def _normalize_metrics(metrics, report):
    normalized = {}

    def visit(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{path}.{key}" if path else key)
            return
        if isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
            return
        normalized[path] = {
            "value": value,
            "unit": _metric_unit(path),
            "source_report_id": report.pk,
            "source_report": report.name,
            "project_id": report.project_id,
            "extraction_status": "unavailable" if value is None else "extracted",
            "stage": report.stage,
            "device_part": report.device_part or None,
            "run_id": str(report.run_id),
            "timestamp": report.uploaded_at.isoformat(),
        }

    visit(metrics, "")
    return normalized


def _analysis_coverage(report):
    if not report:
        return {"state": "NOT_ANALYZED", "report_id": None}
    if report.report_type == Report.ReportType.TIMING:
        return {
            "report_id": report.pk,
            "run_id": str(report.run_id),
            "stage": report.stage,
            **_clean_clock_fields(report.analysis_coverage or {}),
        }
    metrics = report.parsed_metrics or {}
    if report.report_type == Report.ReportType.POWER:
        present = [key for key, value in metrics.items() if value is not None]
        return {
            "report_id": report.pk,
            "run_id": str(report.run_id),
            "stage": report.stage,
            "reported_fields": present,
            "power_budget_available": metrics.get("design_power_budget_w") is not None,
            "confidence": metrics.get("confidence"),
        }
    resource_fields = (
        "slice_luts", "slice_registers", "block_ram_tiles", "dsp_blocks", "bonded_iobs"
    )
    return {
        "report_id": report.pk,
        "run_id": str(report.run_id),
        "stage": report.stage,
        "resources_with_used_and_available": [
            key for key in resource_fields
            if isinstance(metrics.get(key), dict)
            and metrics[key].get("used") is not None
            and metrics[key].get("available") is not None
        ],
    }


def _serialize_report(report, *, include_metrics=True):
    coverage = _clean_clock_fields(report.analysis_coverage)
    result = {
        "id": report.pk,
        "project_id": report.project_id,
        "run_id": str(report.run_id),
        "stage": report.stage,
        "device_part": report.device_part or None,
        "name": report.name,
        "source_report": report.name,
        "source_design": (report.source_metadata or {}).get("design") or None,
        "report_type": report.report_type,
        "status": report.status,
        "extraction_status": report.status,
        "uploaded_at": report.uploaded_at.isoformat(),
        "error": report.error_message or None,
        "analysis_coverage": coverage,
    }
    if include_metrics:
        metrics = dict(report.parsed_metrics or {})
        if report.report_type == Report.ReportType.TIMING:
            metrics = _clean_clock_fields(metrics)
            if isinstance(metrics.get("timing_analysis_coverage"), dict):
                metrics["timing_analysis_coverage"] = _clean_clock_fields(metrics["timing_analysis_coverage"])
        result["metrics"] = metrics
        result["normalized_metrics"] = report.normalized_metrics
        result["interpretations"] = interpret_report(report)
    return result


def _latest_project(user, project_id=None):
    if project_id:
        return get_object_or_404(Project, pk=project_id, owner=user)
    latest = (
        Report.objects.filter(
            uploaded_by=user,
            status=Report.Status.COMPLETED,
        )
        .select_related("project")
        .order_by("-uploaded_at")
        .first()
    )
    return latest.project if latest else None


class HealthView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok"})


class ReportUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        files = request.FILES.getlist("files")
        if not files:
            return Response({"error": "Select at least one .rpt file."}, status=status.HTTP_400_BAD_REQUEST)
        if len(files) > MAX_REPORTS_PER_REQUEST:
            return Response(
                {"error": f"A maximum of {MAX_REPORTS_PER_REQUEST} reports can be parsed at once."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        selected_project_id = request.data.get("project_id")
        selected_project = (
            get_object_or_404(Project, pk=selected_project_id, owner=request.user)
            if selected_project_id else None
        )

        results = []
        run_id = uuid4()
        for uploaded in files:
            filename = Path(uploaded.name).name
            metadata = {}
            ext_ok = Path(filename).suffix.lower() == ".rpt"
            size_ok = uploaded.size <= MAX_REPORT_SIZE

            if not size_ok:
                results.append({
                    "name": filename,
                    "status": "failed",
                    "error": f"File exceeds the {MAX_REPORT_SIZE // (1024 * 1024)} MB size limit.",
                })
                continue

            if not ext_ok:
                results.append({
                    "name": filename,
                    "status": "failed",
                    "error": "Only .rpt files are accepted.",
                })
                continue

            try:
                uploaded.seek(0)
                text = uploaded.read().decode("utf-8", errors="replace")
                metadata = extract_metadata(text)
                command_result = parse_report(text)
                report_type = command_result.get("report_type") or {
                    "report_timing": "timing",
                    "report_timing_summary": "timing",
                    "report_power": "power",
                    "report_utilization": "utilization",
                }.get(command_result.get("generating_command"))
                if report_type not in SUPPORTED_TYPES:
                    raise ValueError(f"Unsupported report type: {report_type!r}.")
                metrics = {
                    key: value
                    for key, value in command_result.items()
                    if key not in {"report_type", "metadata", "generating_command"}
                }
                project = selected_project or _project_for(
                    request.user,
                    metadata.get("design") or f"unidentified-{Path(filename).stem}",
                )
                with transaction.atomic():
                    uploaded.seek(0)
                    report = Report.objects.create(
                        project=project,
                        run_id=run_id,
                        stage=_stage_for(metadata),
                        device_part=metadata.get("device", "")[:120],
                        uploaded_by=request.user,
                        name=filename,
                        file=uploaded,
                        report_type=report_type,
                        status=Report.Status.PROCESSING,
                        analysis_coverage=(
                            metrics.get("timing_analysis_coverage", {})
                            if report_type == Report.ReportType.TIMING else {}
                        ),
                        source_metadata=metadata,
                    )
                    report.parsed_metrics = metrics
                    report.normalized_metrics = _normalize_metrics(metrics, report)
                    report.status = Report.Status.COMPLETED
                    report.save(update_fields=["parsed_metrics", "normalized_metrics", "status"])
                results.append(_serialize_report(report))
            except Exception as exc:
                # Persist parse failures as failed records, never as successful metrics.
                try:
                    project = _project_for(
                        request.user,
                        metadata.get("design") or f"unparsed-{Path(filename).stem}",
                    )
                    uploaded.seek(0)
                    report = Report.objects.create(
                        project=project,
                        run_id=run_id,
                        stage=_stage_for(metadata),
                        device_part=metadata.get("device", "")[:120],
                        uploaded_by=request.user,
                        name=filename,
                        file=uploaded,
                        report_type=Report.ReportType.OTHER,
                        status=Report.Status.FAILED,
                        analysis_coverage={},
                        source_metadata=metadata,
                        error_message=str(exc)[:2000],
                    )
                    results.append(_serialize_report(report, include_metrics=False))
                except Exception as save_exc:
                    results.append({
                        "name": filename,
                        "status": "failed",
                        "error": f"{str(exc)[:500]} (could not save report: {str(save_exc)[:500]})",
                    })

        return Response({"reports": results}, status=status.HTTP_200_OK)


class DashboardDataView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        projects = list(
            Project.objects.filter(owner=request.user)
            .order_by("name")
            .values("id", "name")
        )
        project = _latest_project(request.user, request.query_params.get("project_id"))
        if project is None:
            design_health, readiness = evaluate_design_health({})
            return Response({
                "project": None,
                "projects": projects,
                "latest": {kind: None for kind in SUPPORTED_TYPES},
                "runs": [],
                "active_run_id": None,
                "analysis_coverage": {
                    kind: {"state": "NOT_ANALYZED", "report_id": None}
                    for kind in SUPPORTED_TYPES
                } | {"timing_readiness": readiness},
                "interpretations": {
                    kind: interpret_report(None, kind, readiness if kind == "timing" else None)
                    for kind in SUPPORTED_TYPES
                },
                "stage_compatibility": {"compatible": None, "stages": {}, "devices": {}, "designs": {}, "reason": "No report stages, designs, or devices are available to compare."},
                "design_health": design_health,
                "reports": [],
            })

        reports = list(
            Report.objects.filter(project=project, uploaded_by=request.user)
            .order_by("-uploaded_at", "-pk")
        )
        runs_by_id = {}
        for item in reports:
            run_key = str(item.run_id)
            run = runs_by_id.setdefault(run_key, {
                "id": run_key,
                "uploaded_at": item.uploaded_at.isoformat(),
                "report_count": 0,
                "report_types": [],
            })
            run["report_count"] += 1
            if item.report_type not in run["report_types"]:
                run["report_types"].append(item.report_type)
        runs = list(runs_by_id.values())
        requested_run_id = request.query_params.get("run_id")
        active_run_id = requested_run_id if requested_run_id in runs_by_id else (runs[0]["id"] if runs else None)
        active_reports = [item for item in reports if str(item.run_id) == active_run_id] if active_run_id else []
        latest = {kind: None for kind in SUPPORTED_TYPES}
        latest_records = {kind: None for kind in SUPPORTED_TYPES}
        for report in active_reports:
            if report.status == Report.Status.COMPLETED and latest.get(report.report_type) is None:
                latest[report.report_type] = _serialize_report(report)
                latest_records[report.report_type] = report

        design_health, timing_readiness_result = evaluate_design_health(latest_records)
        analysis_coverage = {
            kind: _analysis_coverage(latest_records.get(kind))
            for kind in SUPPORTED_TYPES
        }
        analysis_coverage["timing_readiness"] = timing_readiness_result
        interpretations = {
            kind: interpret_report(latest_records.get(kind), kind, timing_readiness_result if kind == "timing" else None)
            for kind in SUPPORTED_TYPES
        }
        contributing = [report for report in latest_records.values() if report]
        stages = sorted({report.stage for report in contributing})
        devices = sorted({report.device_part for report in contributing if report.device_part})
        designs = sorted({str((report.source_metadata or {}).get("design")) for report in contributing if (report.source_metadata or {}).get("design")})
        unknown_device = any(not report.device_part for report in contributing)
        unknown_design = any(not (report.source_metadata or {}).get("design") for report in contributing)
        compatible = (
            len(stages) <= 1 and "unknown" not in stages and len(devices) <= 1 and not unknown_device
            and len(designs) <= 1 and not unknown_design
        ) if contributing else None
        stage_compatibility = {
            "compatible": compatible,
            "stages": {report.report_type: report.stage for report in contributing},
            "devices": {report.report_type: report.device_part or None for report in contributing},
            "designs": {report.report_type: (report.source_metadata or {}).get("design") or None for report in contributing},
            "reason": (
                "Latest reports have different source designs, stages, or devices; metrics remain separate and no combined health score is calculated."
                if len(designs) > 1 else
                "Report source design is missing, so design compatibility cannot be verified."
                if unknown_design else
                "Latest reports have different stages or devices; metrics remain separate and no combined health score is calculated."
                if len(stages) > 1 else
                "Report stage is unknown, so stage compatibility cannot be verified."
                if "unknown" in stages else
                "Report device information is missing, so hardware compatibility cannot be verified."
                if unknown_device else
                "Latest reports use compatible stages and device information."
            ),
        }

        return Response({
            "project": {"id": project.pk, "name": project.name},
            "projects": projects,
            "latest": latest,
            "runs": runs,
            "active_run_id": active_run_id,
            "analysis_coverage": analysis_coverage,
            "interpretations": interpretations,
            "stage_compatibility": stage_compatibility,
            "design_health": design_health,
            "reports": [_serialize_report(report) for report in reports],
        })
