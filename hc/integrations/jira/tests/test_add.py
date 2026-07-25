from __future__ import annotations

import json

from django.test.utils import override_settings

from hc.api.models import Channel
from hc.test import BaseTestCase


@override_settings(JIRA_ENABLED=True)
class AddJiraTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = f"/projects/{self.project.code}/add_jira/"

    def test_instructions_work(self) -> None:
        self.client.login(username="alice@example.org", password="password")
        r = self.client.get(self.url)
        self.assertContains(r, "Jira")

    def test_it_works(self) -> None:
        form = {
            "url": "https://company.atlassian.net",
            "username": "alice@example.org",
            "token": "api-token",
            "project_key": "PROJ",
            "issue_type": "Bug",
            "close_transition": "Close Issue",
            "labels": "ops, sre",
        }

        self.client.login(username="alice@example.org", password="password")
        r = self.client.post(self.url, form)
        self.assertRedirects(r, self.channels_url)

        c = Channel.objects.get()
        self.assertEqual(c.kind, "jira")
        self.assertEqual(c.project, self.project)
        self.assertEqual(c.value, json.dumps(
            {
                "close_transition": "Close Issue",
                "issue_type": "Bug",
                "labels": ["ops", "sre"],
                "project_key": "PROJ",
                "token": "api-token",
                "url": "https://company.atlassian.net",
                "username": "alice@example.org",
            },
            sort_keys=True,
        ))

    def test_it_handles_empty_labels(self) -> None:
        form = {
            "url": "https://company.atlassian.net",
            "username": "alice@example.org",
            "token": "api-token",
            "project_key": "PROJ",
            "issue_type": "Task",
            "close_transition": "Done",
            "labels": "",
        }

        self.client.login(username="alice@example.org", password="password")
        self.client.post(self.url, form)

        c = Channel.objects.get()
        doc = json.loads(c.value)
        self.assertEqual(doc["labels"], [])

    def test_it_requires_url(self) -> None:
        form = {
            "url": "",
            "username": "alice@example.org",
            "token": "api-token",
            "project_key": "PROJ",
            "issue_type": "Task",
            "close_transition": "Done",
        }

        self.client.login(username="alice@example.org", password="password")
        r = self.client.post(self.url, form)
        self.assertEqual(r.status_code, 400)

    @override_settings(JIRA_ENABLED=False)
    def test_it_requires_jira_enabled(self) -> None:
        self.client.login(username="alice@example.org", password="password")
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 404)

    def test_it_requires_rw_access(self) -> None:
        self.bobs_membership.role = "r"
        self.bobs_membership.save()

        self.client.login(username="bob@example.org", password="password")
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 403)
