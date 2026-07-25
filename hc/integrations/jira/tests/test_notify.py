from __future__ import annotations

import json
from datetime import timedelta as td
from typing import Any
from unittest.mock import Mock, patch

from django.test.utils import override_settings
from django.utils.timezone import now

from hc.api.models import TRANSPORTS, Channel, Check, Flip, Notification, Ping
from hc.integrations.jira.transport import Jira
from hc.test import BaseTestCase


@override_settings(JIRA_ENABLED=True)
class NotifyJiraTestCase(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()

        self.check = Check(project=self.project)
        self.check.name = "Foo"
        self.check.status = "paused"
        self.check.last_ping = now()
        self.check.save()

        self.ping = Ping(owner=self.check)
        self.ping.created = now() - td(minutes=10)
        self.ping.n = 112233
        self.ping.save()

        self.channel = Channel(project=self.project)
        self.channel.kind = "jira"
        self.channel.value = json.dumps(
            {
                "url": "https://company.atlassian.net",
                "username": "alice@example.org",
                "token": "api-token",
                "project_key": "PROJ",
                "issue_type": "Task",
                "close_transition": "Done",
                "labels": [],
            }
        )
        self.channel.save()
        self.channel.checks.add(self.check)

        self.flip = Flip(owner=self.check)
        self.flip.created = now()
        self.flip.old_status = "new"
        self.flip.new_status = "down"

        self._orig_transport = TRANSPORTS.get("jira")
        TRANSPORTS["jira"] = ("Jira", Jira)

    def tearDown(self) -> None:
        if self._orig_transport is None:
            TRANSPORTS.pop("jira", None)
        else:
            TRANSPORTS["jira"] = self._orig_transport

    def _make_response(self, payload: dict[str, Any] | None = None, status_code: int = 200) -> Mock:
        r = Mock()
        r.status_code = status_code
        r.content = json.dumps(payload or {}).encode()
        r.json.return_value = payload or {}
        return r

    @patch("hc.lib.curl.request", autospec=True)
    def test_it_creates_issue_on_down(self, mock_request: Mock) -> None:
        mock_request.return_value = self._make_response({"issues": []})

        error = self.channel.notify(self.flip)
        self.assertEqual(error, "")

        self.assertEqual(Notification.objects.count(), 1)

        # Search then create
        self.assertEqual(mock_request.call_count, 2)
        search_call, create_call = mock_request.call_args_list

        self.assertEqual(search_call.args[0], "get")
        self.assertIn("/rest/api/2/search", search_call.args[1])
        params = search_call.kwargs["params"]
        self.assertIn("hc-", params["jql"])
        self.assertIn("statusCategory != Done", params["jql"])

        self.assertEqual(create_call.args[0], "post")
        self.assertIn("/rest/api/2/issue", create_call.args[1])
        payload = create_call.kwargs["json"]
        self.assertEqual(payload["fields"]["project"]["key"], "PROJ")
        self.assertEqual(payload["fields"]["issuetype"]["name"], "Task")
        self.assertEqual(payload["fields"]["summary"], "Down: Foo")
        self.assertIn("hc-", payload["fields"]["labels"][0])

    @patch("hc.lib.curl.request", autospec=True)
    def test_it_adds_comment_when_issue_already_open(self, mock_request: Mock) -> None:
        mock_request.return_value = self._make_response({"issues": [{"key": "PROJ-42"}]})

        error = self.channel.notify(self.flip)
        self.assertEqual(error, "")

        self.assertEqual(Notification.objects.count(), 1)
        # Search + comment
        self.assertEqual(mock_request.call_count, 2)
        comment_call = mock_request.call_args_list[1]
        self.assertEqual(comment_call.args[0], "post")
        self.assertIn("/rest/api/2/issue/PROJ-42/comment", comment_call.args[1])
        self.assertIn("still down", comment_call.kwargs["json"]["body"])

    @patch("hc.lib.curl.request", autospec=True)
    def test_it_transitions_on_up(self, mock_request: Mock) -> None:
        self.flip.old_status = "down"
        self.flip.new_status = "up"

        mock_request.return_value.json.side_effect = [
            {"issues": [{"key": "PROJ-42"}]},
            {
                "transitions": [
                    {
                        "id": "31",
                        "name": "Done",
                        "to": {"statusCategory": {"key": "done"}},
                    }
                ]
            },
        ]
        mock_request.return_value.status_code = 200

        error = self.channel.notify(self.flip)
        self.assertEqual(error, "")

        self.assertEqual(Notification.objects.count(), 1)
        # Search + get transitions + do transition + comment
        self.assertEqual(mock_request.call_count, 4)
        transition_call = mock_request.call_args_list[2]
        self.assertEqual(transition_call.args[0], "post")
        self.assertIn("/rest/api/2/issue/PROJ-42/transitions", transition_call.args[1])
        self.assertEqual(transition_call.kwargs["json"], {"transition": {"id": "31"}})

    @override_settings(JIRA_ENABLED=False)
    def test_it_requires_jira_enabled(self) -> None:
        error = self.channel.notify(self.flip)
        self.assertEqual(error, "Jira notifications are not enabled.")

    @patch("hc.lib.curl.request", autospec=True)
    def test_it_handles_error_response(self, mock_request: Mock) -> None:
        mock_request.return_value = self._make_response(
            {"errorMessages": ["Project not found"]}, status_code=400
        )

        error = self.channel.notify(self.flip)
        self.assertIn("Project not found", error)
