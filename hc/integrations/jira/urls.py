from __future__ import annotations

from django.urls import path

from hc.integrations.jira import views

urlpatterns = [
    path("projects/<uuid:code>/add_jira/", views.add, name="hc-add-jira"),
]
