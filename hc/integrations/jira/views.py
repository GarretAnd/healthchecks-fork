from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect, render

from hc.accounts.http import AuthenticatedHttpRequest
from hc.api.models import Channel
from hc.front.decorators import require_setting
from hc.front.views import _get_rw_project_for_user
from hc.integrations.jira.forms import JiraForm


def jira_form(request: AuthenticatedHttpRequest, channel: Channel) -> HttpResponse:
    adding = channel._state.adding
    if request.method == "POST":
        form = JiraForm(request.POST)
        if form.is_valid():
            channel.value = form.get_value()
            channel.save()

            if adding:
                channel.assign_all_checks()
            return redirect("hc-channels", channel.project.code)
        return HttpResponseBadRequest()
    elif adding:
        form = JiraForm()
    else:
        conf = channel.json
        form = JiraForm(
            {
                "url": conf.get("url", ""),
                "username": conf.get("username", ""),
                "token": conf.get("token", ""),
                "project_key": conf.get("project_key", ""),
                "issue_type": conf.get("issue_type", "Task"),
                "close_transition": conf.get("close_transition", "Done"),
                "labels": ", ".join(conf.get("labels", [])),
            }
        )

    ctx = {"page": "channels", "project": channel.project, "form": form}
    return render(request, "add_jira.html", ctx)


@require_setting("JIRA_ENABLED")
@login_required
def add(request: AuthenticatedHttpRequest, code: UUID) -> HttpResponse:
    project = _get_rw_project_for_user(request, code)
    channel = Channel(project=project, kind="jira")
    return jira_form(request, channel)
