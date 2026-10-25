from django.conf import settings
from django.db import models
import uuid

class Report(models.Model):
    class Stage(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        SYNTHESIS = "synthesis", "Synthesis"
        IMPLEMENTATION = "implementation", "Implementation"
        ROUTE_DESIGN = "route_design", "Route Design"

    class ReportType(models.TextChoices):
        TIMING = "timing", "Timing Summary"
        UTILIZATION = "utilization", "Utilization"
        POWER = "power", "Power"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="reports",
    )
    run_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.UNKNOWN)
    device_part = models.CharField(max_length=120, blank=True)

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="uploaded_vlsi_reports",
    )

    name = models.CharField(max_length=255)
    file = models.FileField(upload_to="reports/%Y/%m/")

    report_type = models.CharField(
        max_length=20,
        choices=ReportType.choices,
        default=ReportType.OTHER,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UPLOADED,
    )

    uploaded_at = models.DateTimeField(auto_now_add=True)
    error_message = models.TextField(blank=True)
    parsed_metrics = models.JSONField(null=True, blank=True)
    normalized_metrics = models.JSONField(default=dict, blank=True)
    analysis_coverage = models.JSONField(default=dict, blank=True)
    source_metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name

