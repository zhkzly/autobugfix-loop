from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from autobugfix.models import utc_now
from autobugfix.operator.models import OperatorRequest, OperatorReview, OperatorTriage


class OperatorStoreError(RuntimeError):
    pass


def _safe_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip())
    return safe.strip("-") or "operator"


class OperatorStore:
    def __init__(self, project_root: Path | str = ".") -> None:
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / ".autobugfix/operator"

    def init(self) -> None:
        for name in ("triage", "requests", "reviews", "validations", "baselines"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def next_id(self, prefix: str = "op") -> str:
        stamp = utc_now().replace(":", "").replace("-", "").replace("Z", "")
        return f"{prefix}-{stamp}"

    def _write_yaml(self, path: Path, data: dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise OperatorStoreError(f"missing operator record: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise OperatorStoreError(f"operator record must be a mapping: {path}")
        return data

    def write_triage(self, triage: OperatorTriage) -> Path:
        self.init()
        return self._write_yaml(self.root / "triage" / f"{_safe_id(triage.triage_id)}.yaml", triage.to_dict())

    def read_triage(self, triage_id: str) -> OperatorTriage:
        return OperatorTriage.from_dict(self._read_yaml(self.root / "triage" / f"{_safe_id(triage_id)}.yaml"))

    def write_request(self, request: OperatorRequest) -> Path:
        self.init()
        return self._write_yaml(self.root / "requests" / f"{_safe_id(request.request_id)}.yaml", request.to_dict())

    def read_request(self, request_id: str) -> OperatorRequest:
        return OperatorRequest.from_dict(self._read_yaml(self.root / "requests" / f"{_safe_id(request_id)}.yaml"))

    def write_review(self, review: OperatorReview) -> Path:
        self.init()
        stamp = utc_now().replace(":", "").replace("-", "").replace("Z", "")
        name = f"{_safe_id(review.request_id)}-{stamp}-{_safe_id(review.reviewer)}.yaml"
        return self._write_yaml(self.root / "reviews" / name, review.to_dict())

    def read_reviews(self, request_id: str) -> list[OperatorReview]:
        self.init()
        prefix = f"{_safe_id(request_id)}-"
        reviews: list[OperatorReview] = []
        for path in sorted((self.root / "reviews").glob(f"{prefix}*.yaml")):
            reviews.append(OperatorReview.from_dict(self._read_yaml(path)))
        return reviews

    def write_validation(self, request_id: str, data: dict[str, Any]) -> Path:
        self.init()
        stamp = utc_now().replace(":", "").replace("-", "").replace("Z", "")
        return self._write_yaml(self.root / "validations" / f"{_safe_id(request_id)}-{stamp}.yaml", data)
