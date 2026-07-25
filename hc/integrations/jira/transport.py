from __future__ import annotations

import json
from typing import Any, NoReturn, cast

from django.conf import settings

from hc.api.models import Flip, Notification
from hc.api.transports import HttpTransport, TransportError, get_ping_body
from hc.lib import curl


class Jira(HttpTransport):
    def is_noop(self, status: str) -> bool:
        return False

    @classmethod
    def raise_for_response(cls, response: curl.Response) -> NoReturn:
        message = f"Received status code {response.status_code}"
        try:
            doc = response.json()
            if isinstance(doc, dict):
                doc = cast(dict[str, Any], doc)
                if doc.get("errorMessages"):
                    error_messages = cast(list[str], doc["errorMessages"])
                    message += ": " + ", ".join(str(m) for m in error_messages)
                elif doc.get("errors"):
                    message += ": " + json.dumps(doc["errors"])
        except Exception:
            pass

        raise TransportError(message)

    def _jira_request(
        self,
        method: str,
        url: str,
        *,
        params: curl.Params = None,
        json: Any = None,
        headers: curl.Headers = None,
        auth: curl.Auth = None,
        retry: bool = True,
    ) -> curl.Response:
        tries_left = 3 if retry else 1
        last_error: TransportError | None = None

        while tries_left > 0:
            try:
                r = curl.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    auth=auth,
                    timeout=30,
                )
            except curl.CurlError as e:
                raise TransportError(e.message)
            if r.status_code not in (200, 201, 202, 204):
                self.raise_for_response(r)
            return r
            except TransportError as e:
                last_error = e
                tries_left -= 1
                if not retry or tries_left == 0:
                    raise

        assert last_error is not None
        raise last_error

    def notify(self, flip: Flip, notification: Notification) -> None:
        if not getattr(settings, "JIRA_ENABLED", False):
            raise TransportError("Jira notifications are not enabled.")

        conf = self.channel.json
        base_url = conf["url"].rstrip("/")
        auth = (conf["username"], conf["token"])
        project_key = conf["project_key"]
        issue_type = conf.get("issue_type") or "Task"
        close_transition = conf.get("close_transition") or "Done"
        extra_labels = conf.get("labels") or []

        check = flip.owner
        unique_label = f"hc-{check.unique_key}"
        labels = [unique_label] + [label for label in extra_labels if label]

        ping = self.last_ping(flip)
        ctx = {
            "flip": flip,
            "check": check,
            "status": flip.new_status,
            "ping": ping,
        }

        body = get_ping_body(ping, maxlen=1000)
        if body:
            ctx["body"] = body

        summary = self.tmpl("jira_summary.html", **ctx)
        description = self.tmpl("jira_description.html", **ctx)

        if flip.new_status == "down":
            existing = self._find_open_issue(base_url, auth, unique_label)
            if existing:
                self._add_comment(
                    base_url, auth, existing, f"Check is still down: {summary}"
                )
            else:
                self._create_issue(
                    base_url,
                    auth,
                    project_key,
                    issue_type,
                    labels,
                    summary,
                    description,
                )

        elif flip.new_status == "up":
            existing = self._find_open_issue(base_url, auth, unique_label)
            if existing:
                self._transition(base_url, auth, existing, close_transition)
                self._add_comment(base_url, auth, existing, f"Resolved: {summary}")

    def _find_open_issue(
        self, base_url: str, auth: curl.Auth, label: str
    ) -> str | None:
        jql = f'labels = "{label}" AND statusCategory != Done'
        url = f"{base_url}/rest/api/2/search"

        r = self._jira_request(
            "get",
            url,
            params={"jql": jql, "fields": "key", "maxResults": "1"},
            headers={"Content-Type": "application/json"},
            auth=auth,
            retry=True,
        )

        data: Any = r.json()
        issues = data.get("issues", [])
        if issues:
            return str(issues[0]["key"])
        return None

    def _create_issue(
        self,
        base_url: str,
        auth: curl.Auth,
        project_key: str,
        issue_type: str,
        labels: list[str],
        summary: str,
        description: str,
    ) -> None:
        url = f"{base_url}/rest/api/2/issue"
        payload: dict[str, Any] = {
            "fields": {
                "project": {"key": project_key},
                "issuetype": {"name": issue_type},
                "summary": summary,
                "description": description,
                "labels": labels,
            }
        }

        self._jira_request(
            "post",
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            auth=auth,
            retry=True,
        )

    def _add_comment(
        self, base_url: str, auth: curl.Auth, issue_key: str, body: str
    ) -> None:
        url = f"{base_url}/rest/api/2/issue/{issue_key}/comment"
        self._jira_request(
            "post",
            url,
            json={"body": body},
            headers={"Content-Type": "application/json"},
            auth=auth,
            retry=True,
        )

    def _transition(
        self,
        base_url: str,
        auth: curl.Auth,
        issue_key: str,
        transition_name: str,
    ) -> None:
        url = f"{base_url}/rest/api/2/issue/{issue_key}/transitions"
        r = self._jira_request(
            "get",
            url,
            params={"expand": "transitions.fields"},
            headers={"Content-Type": "application/json"},
            auth=auth,
            retry=True,
        )

        data: Any = r.json()
        transitions = data.get("transitions", [])

        transition_id = None
        for t in transitions:
            if t.get("name") == transition_name:
                transition_id = t.get("id")
                break

        if not transition_id:
            for t in transitions:
                to = t.get("to") or {}
                status_category = to.get("statusCategory") or {}
                if status_category.get("key") == "done":
                    transition_id = t.get("id")
                    break

        if not transition_id:
            raise TransportError(f"Could not find a close transition for {issue_key}")

        url = f"{base_url}/rest/api/2/issue/{issue_key}/transitions"
        self._jira_request(
            "post",
            url,
            json={"transition": {"id": transition_id}},
            headers={"Content-Type": "application/json"},
            auth=auth,
            retry=True,
        )
