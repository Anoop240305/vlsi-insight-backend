from django.db import models


class AnalysisRun(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    report = models.ForeignKey(
        "reports.Report",
        on_delete=models.CASCADE,
        related_name="analysis_runs",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    parser_version = models.CharField(max_length=50, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Analysis {self.id} - Report {self.report_id}"

class TimingMetrics(models.Model):

    analysis_run = models.OneToOneField(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="timing_metrics",
    )

    wns_ns = models.FloatField(null=True, blank=True)
    tns_ns = models.FloatField(null=True, blank=True)
    whs_ns = models.FloatField(null=True, blank=True)
    ths_ns = models.FloatField(null=True, blank=True)

    source_section = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"Timing metrics - Analysis {self.analysis_run_id}"

class UtilizationMetrics(models.Model):

    analysis_run = models.OneToOneField(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="utilization_metrics",
    )

    lut_used = models.PositiveIntegerField(null=True, blank=True)
    lut_available = models.PositiveIntegerField(null=True, blank=True)

    ff_used = models.PositiveIntegerField(null=True, blank=True)
    ff_available = models.PositiveIntegerField(null=True, blank=True)

    bram_used = models.FloatField(null=True, blank=True)
    dsp_used = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return f"Utilization metrics - Analysis {self.analysis_run_id}"

class Finding(models.Model):

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="findings",
    )

    category = models.CharField(max_length=50)
    severity = models.CharField(
        max_length=20,
        choices=Severity.choices,
    )

    title = models.CharField(max_length=255)
    description = models.TextField()

    evidence = models.JSONField(default=dict, blank=True)
    recommendation = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class AIInsight(models.Model):

    analysis_run = models.ForeignKey(
        AnalysisRun,
        on_delete=models.CASCADE,
        related_name="ai_insights",
    )

    summary = models.TextField()
    explanation = models.TextField()
    recommendation = models.TextField(blank=True)

    model_name = models.CharField(max_length=100, blank=True)
    evidence = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"AI Insight - Analysis {self.analysis_run_id}"


# Create your models here.
