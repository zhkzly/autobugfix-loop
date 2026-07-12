from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.eval.benchmarks.models import digest_file, record_with_digest
from autobugfix.models import utc_now


class IssueEvidenceError(RuntimeError):
    pass


_URL_PATTERN = re.compile(r"https?://[^\s)\]>]+")


class _GitHubStructuredDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._parts: list[str] = []
        self.payloads: list[Mapping[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self._capture = tag == "script" and attributes.get("type") == "application/ld+json"
        if self._capture:
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "script" or not self._capture:
            return
        self._capture = False
        try:
            payload = json.loads("".join(self._parts))
        except json.JSONDecodeError:
            return
        if isinstance(payload, Mapping):
            self.payloads.append(payload)


@dataclass(slots=True, frozen=True)
class IssueEvidence:
    tracker: str
    report_id: str
    report_url: str
    api_url: str
    title: str
    body: str
    attachment_uris: tuple[str, ...]
    raw_path: str
    raw_sha256: str
    fetched_at: str

    def to_dict(self) -> dict[str, Any]:
        return record_with_digest(
            {
                "tracker": self.tracker,
                "report_id": self.report_id,
                "report_url": self.report_url,
                "api_url": self.api_url,
                "title": self.title,
                "body": self.body,
                "attachment_uris": list(self.attachment_uris),
                "raw_path": self.raw_path,
                "raw_sha256": self.raw_sha256,
                "fetched_at": self.fetched_at,
            }
        )


class IssueEvidenceFetcher:
    def __init__(self, timeout_seconds: int):
        if timeout_seconds < 1:
            raise ValueError("issue timeout must be positive")
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _endpoint(report_url: str, report_id: str) -> tuple[str, str]:
        parsed = urllib.parse.urlparse(report_url)
        host = parsed.netloc.lower()
        parts = [part for part in parsed.path.split("/") if part]
        if host == "github.com" and len(parts) >= 4 and parts[2] in {
            "issues",
            "pull",
            "pulls",
        }:
            owner, repo, _, number = parts[:4]
            return "github", f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
        if host == "issues.apache.org" and report_id:
            return "jira", f"https://issues.apache.org/jira/rest/api/2/issue/{report_id}"
        if host == "storage.googleapis.com" and report_url.endswith(".json"):
            return "google-code-archive", report_url
        if host == "sourceforge.net" and len(parts) >= 4 and parts[0] == "p":
            return "sourceforge", f"https://sourceforge.net/rest/{'/'.join(parts[:4])}"
        raise IssueEvidenceError(f"unsupported issue tracker URL: {report_url}")

    def _request_json(self, url: str) -> Mapping[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Autobugfix-Benchmark-Evidence/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IssueEvidenceError(f"issue tracker request failed: {exc}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IssueEvidenceError("issue tracker did not return JSON") from exc
        if not isinstance(data, Mapping):
            raise IssueEvidenceError("issue tracker JSON must be an object")
        return data

    def _request_github_html(self, url: str) -> tuple[Mapping[str, Any], bytes]:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Autobugfix-Benchmark-Evidence/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IssueEvidenceError(f"GitHub issue HTML request failed: {exc}") from exc
        parser = _GitHubStructuredDataParser()
        parser.feed(raw.decode("utf-8", errors="replace"))
        for payload in parser.payloads:
            if payload.get("@type") == "DiscussionForumPosting" and payload.get("headline"):
                return payload, raw
        raise IssueEvidenceError("GitHub issue HTML has no DiscussionForumPosting data")

    @staticmethod
    def _normalize(
        tracker: str, data: Mapping[str, Any]
    ) -> tuple[str, str, tuple[str, ...]]:
        attachments: list[str] = []
        if tracker == "github":
            title = str(data.get("title") or data.get("headline") or "").strip()
            body = str(data.get("body") or data.get("articleBody") or "").strip()
            attachments.extend(_URL_PATTERN.findall(body))
        elif tracker == "jira":
            fields = data.get("fields")
            if not isinstance(fields, Mapping):
                raise IssueEvidenceError("JIRA response has no fields mapping")
            title = str(fields.get("summary") or "").strip()
            description = fields.get("description")
            body = description if isinstance(description, str) else json.dumps(description or "")
            raw_attachments = fields.get("attachment") or []
            if isinstance(raw_attachments, list):
                attachments.extend(
                    str(item.get("content"))
                    for item in raw_attachments
                    if isinstance(item, Mapping) and item.get("content")
                )
        elif tracker == "sourceforge":
            ticket = data.get("ticket") if isinstance(data.get("ticket"), Mapping) else data
            title = str(ticket.get("summary") or "").strip()
            body = str(ticket.get("description") or "").strip()
            attachments.extend(_URL_PATTERN.findall(body))
        else:
            issue = data.get("issue") if isinstance(data.get("issue"), Mapping) else data
            title = str(issue.get("summary") or issue.get("title") or "").strip()
            body = str(issue.get("description") or issue.get("body") or "").strip()
            attachments.extend(_URL_PATTERN.findall(body))
        if not title:
            raise IssueEvidenceError("issue tracker response has no title")
        return title, body, tuple(dict.fromkeys(attachments))

    def fetch(
        self,
        *,
        report_url: str,
        report_id: str,
        artifact_dir: Path,
    ) -> IssueEvidence:
        tracker, api_url = self._endpoint(report_url, report_id)
        raw_html: bytes | None = None
        try:
            data = self._request_json(api_url)
        except IssueEvidenceError:
            if tracker != "github":
                raise
            data, raw_html = self._request_github_html(report_url)
        title, body, attachments = self._normalize(tracker, data)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if raw_html is None:
            raw_path = artifact_dir / "issue.raw.json"
            raw_path.write_text(
                json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        else:
            raw_path = artifact_dir / "issue.raw.html"
            raw_path.write_bytes(raw_html)
        evidence = IssueEvidence(
            tracker=tracker,
            report_id=report_id,
            report_url=report_url,
            api_url=api_url,
            title=title,
            body=body,
            attachment_uris=attachments,
            raw_path=str(raw_path.resolve()),
            raw_sha256=digest_file(raw_path),
            fetched_at=utc_now(),
        )
        (artifact_dir / "issue.yaml").write_text(
            yaml.safe_dump(evidence.to_dict(), sort_keys=False),
            encoding="utf-8",
        )
        return evidence


def visible_problem_statement(receipt: Any) -> tuple[str, list[dict[str, str]]]:
    """Project a receipt into the issue/evidence text visible to repair agents."""

    issue_path = Path(receipt.issue_evidence_path)
    attachments: list[dict[str, str]] = []
    title = f"Repair Defects4J {receipt.project}-{receipt.bug_id}"
    body = ""
    if issue_path.is_file():
        data = yaml.safe_load(issue_path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            title = str(data.get("title") or title)
            body = str(data.get("body") or "")
            for uri in data.get("attachment_uris") or []:
                attachments.append(
                    {
                        "kind": "upstream-attachment",
                        "uri": str(uri),
                        "description": "Attachment referenced by the upstream issue",
                    }
                )
    trigger_text = "\n".join(f"- {item}" for item in receipt.triggering_tests)
    failure_text = ""
    if receipt.failure_evidence_path != "unavailable":
        failure_path = Path(receipt.failure_evidence_path)
        if failure_path.is_file():
            failure_text = failure_path.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
    reproduction = (
        receipt.reproduction_command
        if receipt.reproduction_command != "unavailable"
        else "defects4j test -w /workspace"
    )
    problem = "\n\n".join(
        part
        for part in (
            title,
            body,
            "Official triggering tests:\n" + trigger_text,
            "Pinned reproduction command:\n" + reproduction,
            (
                "Observed buggy failure output and stack trace:\n" + failure_text
                if failure_text
                else ""
            ),
            "Modify production source only. The Execution verifier will run only the declared visible triggering tests.",
        )
        if part.strip()
    )
    return problem, attachments
