from django.contrib import admin
from django.urls import path
from accounts.views import ProfileView, TokenLoginView
from reports.api_views import DashboardDataView, HealthView, ReportUploadView


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", HealthView.as_view(), name="api-health"),
    path("api/token/", TokenLoginView.as_view(), name="api-token"),
    path("api/profile/", ProfileView.as_view(), name="api-profile"),
    path("api/reports/upload/", ReportUploadView.as_view(), name="report-upload"),
    path("api/dashboard/", DashboardDataView.as_view(), name="dashboard-data"),
]
