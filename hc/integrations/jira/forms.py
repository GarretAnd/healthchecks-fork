from __future__ import annotations

import json
from typing import cast

from django import forms

from hc.front.forms import LaxURLField


class JiraForm(forms.Form):
    error_css_class = "has-error"

    url = LaxURLField(max_length=1000, assume_scheme="https")
    username = forms.CharField(max_length=200)
    token = forms.CharField(max_length=500)
    project_key = forms.CharField(max_length=20)
    issue_type = forms.CharField(max_length=50, initial="Task")
    close_transition = forms.CharField(max_length=50, initial="Done")
    labels = forms.CharField(max_length=500, required=False)

    def clean_url(self) -> str:
        url: str = cast(str, self.cleaned_data["url"])
        return url.rstrip("/")

    def clean_labels(self) -> list[str]:
        result = []
        labels: str = cast(str, self.cleaned_data["labels"] or "")
        for part in labels.split(","):
            part = part.strip()
            if part:
                result.append(part)
        return result

    def get_value(self) -> str:
        data = dict(self.cleaned_data)
        # labels is already a list after clean_labels
        return json.dumps(data, sort_keys=True)
