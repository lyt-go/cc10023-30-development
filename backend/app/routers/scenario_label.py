"""业务场景标注的专用接口：规则版本发布、聚类轮次、回执与重放。

通用资源路由只覆盖「列表 / 登记 / 状态动作」，规则版本和聚类轮次属于领域扩展，
单独暴露子路径，方法内部再调用 ``ScenarioLabelService``，业务判断仍留在服务层。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.services import service_for
from app.services.scenario_label import ScenarioLabelService

router = APIRouter(prefix="/api/scenario_label", tags=["业务场景标注"])


class RulePublishPayload(BaseModel):
    label_id: int
    rule_version: str | None = None
    condition: dict[str, Any] | None = None
    field_versions: dict[str, str] | None = None
    segment_label: str | None = None
    publisher: str = "运营"


class RuleDraftPayload(BaseModel):
    condition: dict[str, Any] | None = None
    field_versions: dict[str, str] | None = None
    segment_label: str | None = None
    rule_text: str | None = None


class RoundStartPayload(BaseModel):
    rule_version_id: int | None = None
    rule_version: str | None = None
    sample_ids: list[str] | None = None
    cluster_config_id: int | None = None
    operator: str = "聚类作业"


class RoundActionPayload(BaseModel):
    action: str
    remark: str | None = None


class SampleUpdatePayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ReplayPayload(BaseModel):
    archive: dict[str, Any] | None = None


def _service() -> ScenarioLabelService:
    return service_for("scenario_label")  # type: ignore[return-value]


@router.get("/rules")
def list_rules(status: str | None = Query(default=None)) -> dict[str, Any]:
    """草稿、已生效版本、历史归档分别查询；不传 status 时按三组返回。"""
    return _service().list_rules(status=status)


@router.post("/rules/publish")
def publish_rule(payload: RulePublishPayload) -> dict[str, Any]:
    return _service().publish_rule_version(
        payload.label_id,
        rule_version=payload.rule_version,
        condition=payload.condition,
        field_versions=payload.field_versions,
        segment_label=payload.segment_label,
        publisher=payload.publisher,
    )


@router.post("/rules/drafts/{label_id}")
def update_rule_draft(label_id: int, payload: RuleDraftPayload) -> dict[str, Any]:
    return _service().update_rule_source(
        label_id,
        condition=payload.condition,
        field_versions=payload.field_versions,
        segment_label=payload.segment_label,
        rule_text=payload.rule_text,
    )


@router.get("/rounds")
def list_rounds(status: str | None = Query(default=None)) -> dict[str, Any]:
    return _service().list_rounds(status=status)


@router.post("/rounds")
def start_round(payload: RoundStartPayload) -> dict[str, Any]:
    return _service().start_cluster_round(
        payload.rule_version_id,
        payload.sample_ids,
        rule_version=payload.rule_version,
        cluster_config_id=payload.cluster_config_id,
        operator=payload.operator,
    )


@router.get("/rounds/{round_id}")
def get_round(round_id: int) -> dict[str, Any]:
    return _service().get_round(round_id)


@router.get("/rounds/{round_id}/receipt")
def get_receipt(round_id: int) -> dict[str, Any]:
    return _service().get_receipt(round_id)


@router.post("/rounds/{round_id}/actions")
def run_round_action(round_id: int, payload: RoundActionPayload) -> dict[str, Any]:
    """轮次动作：应用新版（整轮重算）、提交生效、归档。"""
    service = _service()
    action = payload.action.strip()
    if action in ("应用新版", "应用最新规则", "apply_latest"):
        return service.apply_latest_rule(round_id)
    if action in ("提交生效", "提交", "submit"):
        return service.submit_round(round_id)
    if action in ("归档", "archive"):
        return service.archive_round(round_id)
    return {"ok": False, "message": f"动作「{payload.action}」不属于聚类轮次可执行范围"}


@router.post("/rounds/{round_id}/replay")
def replay_round(round_id: int, payload: ReplayPayload | None = None) -> dict[str, Any]:
    return _service().replay_round(round_id, archive=payload.archive if payload else None)


@router.get("/replays")
def list_replays(round_id: int | None = Query(default=None)) -> dict[str, Any]:
    return _service().list_replays(round_id=round_id)


@router.get("/samples")
def list_samples() -> dict[str, Any]:
    return _service().list_samples()


@router.post("/samples/{sample_id}")
def update_sample(sample_id: str, payload: SampleUpdatePayload) -> dict[str, Any]:
    return _service().update_sample(sample_id, payload.values)
