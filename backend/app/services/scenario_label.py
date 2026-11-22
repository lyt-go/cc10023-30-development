"""业务场景标注领域服务：数据分析场景的业务标注、规则表达与知识管理。

在通用标注 CRUD 之上，补充「规则发布 → 聚类分群作业 → 运行回执 → 归档 → 重放」的
业务闭环：

* 发布：把场景标注里已整理好的分群规则固化成一条生效规则版本，连同当时的筛选条件
  和字段版本一起快照，后续编辑草稿不会影响既有版本。
* 运行：一轮聚类作业在开跑时锁定一条完整规则版本，样本标签和运行回执都引用同一个
  版本号；发布新版本不会覆盖已经完成的结果，未提交的轮次可以显式「应用新版」整轮重算。
* 归档：已生效轮次归档后保持不可变。
* 重放：按归档规则和归档样本重新计算，输出与归档名单的差异，原结果保持不动。
"""
from __future__ import annotations

import copy
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


# 内部表名
RULE_TABLE = "scenario_rule_versions"
ROUND_TABLE = "scenario_cluster_rounds"
LABEL_TABLE = "scenario_sample_labels"
SAMPLE_TABLE = "scenario_samples"
REPLAY_TABLE = "scenario_replays"

DRAFT = "草稿"
EFFECTIVE = "已生效"
HISTORICAL = "历史归档"

ROUND_PENDING = "待处理"
ROUND_EFFECTIVE = "已生效"
ROUND_ARCHIVED = "已归档"
ROUND_INVALIDATED = "已作废"

UNMATCHED = "未命中分群"


class ScenarioLabelService(BaseModuleService):
    MODULE = "scenario_label"
    ENTITY = "场景标注"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["标注编号", "场景名称", "关联特征", "业务规则", "标注时间", "标注人", "标注状态", "场景版本"]
    REQUIRED_FIELDS = ["标注编号", "场景名称", "关联特征"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
    NEGATIVE_ACTIONS = []

    # ── 链路6: 聚类配置 → 分群 → 场景标注 ──
    def validate_label_rules(self, label_id: int) -> dict[str, Any]:
        """校验场景标注的业务规则合法性，检查关联特征是否存在且可查询。"""
        label = store.find(self.MODULE, label_id)
        if label is None:
            return {"ok": False}
        feats = label.get("关联特征", "")
        define_rows = store.rows("feature_define")
        matched = [r for r in define_rows if r.get("特征名称", "") in feats.split(",")]
        if len(matched) == 0:
            label["status"] = "待校验"
            label["abnormal"] = True
            return {"ok": False, "reason": "关联特征均不存在于特征定义中"}
        label["status"] = "已生效"
        label["abnormal"] = False
        return {"ok": True, "label_id": label_id, "matched_features": len(matched)}

    def link_to_cluster_segments(self, label_id: int) -> dict[str, Any]:
        """将场景标注与聚类分群关联，形成聚类→分群→标注的完整链路。"""
        label = store.find(self.MODULE, label_id)
        if label is None:
            return {"ok": False}
        cluster_rows = store.rows("cluster_config")
        related = []
        for c in cluster_rows:
            if c.get("特征集") and label.get("关联特征") and any(
                f.strip() in str(c.get("特征集", "")) for f in str(label.get("关联特征", "")).split(",")
            ):
                related.append({"cluster_id": c.get("id"), "algorithm": c.get("算法类型")})
        return {"ok": True, "label_id": label_id, "related_clusters": related, "count": len(related)}

    def export_label_knowledge_base(self) -> dict[str, Any]:
        """导出全量场景标注知识库，按业务主题聚合后供给外部系统查询。"""
        rows = store.rows(self.MODULE)
        by_status = {}
        for r in rows:
            st = r.get("status", "unknown")
            if st not in by_status:
                by_status[st] = []
            by_status[st].append({
                "id": r.get("id"),
                "scene": r.get("场景名称"),
                "features": r.get("关联特征"),
                "rules": r.get("业务规则"),
                "annotator": r.get("标注人"),
            })
        return {"ok": True, "total_labels": len(rows), "by_status": by_status}

    def standardize_label_format(self, label_id: int) -> dict[str, Any]:
        """标准化场景标注的格式，确保规则表达符合统一的标注规范。"""
        label = store.find(self.MODULE, label_id)
        if label is None:
            return {"ok": False}
        raw_rules = label.get("业务规则", "")
        standardized = raw_rules.replace(" ", "_").replace("，", ",").lower()
        label["业务规则"] = standardized
        return {"ok": True, "label_id": label_id, "raw": raw_rules, "standardized": standardized}

    # ── 分群规则发布 ──────────────────────────────────────────────
    def list_rules(self, status: str | None = None) -> dict[str, Any]:
        """分别查询规则草稿、已生效版本和历史归档版本。

        草稿来自场景标注中尚未发布成版本的规则数据；已生效和历史归档来自规则版本表。
        """
        drafts = [self._draft_view(row) for row in store.rows(self.MODULE)
                  if self._is_draft_label(row)]
        effective = [self._effective_view(v) for v in store.internal(RULE_TABLE) if v.get("status") == EFFECTIVE]
        historical = [self._effective_view(v) for v in store.internal(RULE_TABLE) if v.get("status") == HISTORICAL]
        groups = {DRAFT: drafts, EFFECTIVE: effective, HISTORICAL: historical}
        if status:
            if status not in groups:
                return {"ok": False, "message": f"未知规则状态：{status}", "items": [], "total": 0}
            return {"ok": True, "status": status, "items": groups[status], "total": len(groups[status])}
        return {"ok": True, "groups": groups, "total": sum(len(v) for v in groups.values())}

    def publish_rule_version(
        self,
        label_id: int,
        condition: dict[str, Any] | None = None,
        field_versions: dict[str, str] | None = None,
        segment_label: str | None = None,
        rule_version: str | None = None,
        publisher: str = "运营",
    ) -> dict[str, Any]:
        """把场景标注规则数据发布成一条生效规则版本。

        版本里完整保留发布当时的筛选条件、涉及字段的字段版本和分群标签；同一场景之前
        的生效版本自动转为历史归档，但它们的快照和已产生的轮次结果都不会被改写。
        """
        label = store.find(self.MODULE, label_id)
        if label is None:
            return {"ok": False, "message": f"场景标注 {label_id} 不存在"}
        if condition is not None:
            label["筛选条件"] = copy.deepcopy(condition)
        if field_versions is not None:
            label["字段版本"] = copy.deepcopy(field_versions)
        if segment_label is not None:
            label["分群标签"] = segment_label

        rule = self._extract_rule(label)
        missing = self._missing_rule_parts(rule)
        if missing:
            return {"ok": False, "message": f"规则不完整，缺少：{'、'.join(missing)}"}

        versions = store.internal(RULE_TABLE)
        version_number = sum(1 for v in versions if v.get("来源标注编号") == label.get("标注编号")) + 1
        for existing in versions:
            if existing.get("来源标注编号") == label.get("标注编号") and existing.get("status") == EFFECTIVE:
                existing["status"] = HISTORICAL
                existing["archived_at"] = "2026-11-22"
        version_id = store.next_id(RULE_TABLE)
        record = {
            "id": version_id,
            "版本编号": f"RULE-V{version_id:04d}",
            "rule_version": rule_version or f"v{version_number}.0",
            "来源标注编号": label.get("标注编号"),
            "来源标注ID": label_id,
            "场景名称": label.get("场景名称"),
            "规则编号": f"{label.get('标注编号')}-R{version_number:02d}",
            "筛选条件": copy.deepcopy(rule["condition"]),
            "字段版本": copy.deepcopy(rule["field_versions"]),
            "分群标签": rule["segment_label"],
            "命中分群": rule["segment_label"],
            "未命中分群": UNMATCHED,
            "规则完整性": "完整",
            "status": EFFECTIVE,
            "发布人": publisher,
            "发布时间": "2026-11-22",
            "历史归档时间": None,
            "快照": copy.deepcopy(rule),
        }
        versions.append(record)
        label["status"] = EFFECTIVE
        label["pending"] = False
        label["abnormal"] = False
        label["标注状态"] = EFFECTIVE
        label["当前规则版本"] = record["rule_version"]
        return {
            "ok": True,
            "message": f"规则 {record['rule_version']} 已发布生效",
            "version": copy.deepcopy(record),
            "rule_version": record["rule_version"],
            "version_id": version_id,
        }

    def update_rule_source(
        self,
        label_id: int,
        *,
        condition: dict[str, Any] | None = None,
        field_versions: dict[str, str] | None = None,
        segment_label: str | None = None,
        rule_text: str | None = None,
    ) -> dict[str, Any]:
        """修改场景标注上的规则数据（草稿）。

        只改规则来源，不会改写已经发布的版本，也不会触碰任何轮次结果。
        """
        label = store.find(self.MODULE, label_id)
        if label is None:
            return {"ok": False, "message": f"场景标注 {label_id} 不存在"}
        if condition is not None:
            label["筛选条件"] = copy.deepcopy(condition)
        if field_versions is not None:
            label["字段版本"] = copy.deepcopy(field_versions)
        if segment_label is not None:
            label["分群标签"] = segment_label
        if rule_text is not None:
            label["业务规则"] = rule_text
        label["status"] = DRAFT
        label["pending"] = True
        return {"ok": True, "message": "规则草稿已更新，需重新发布后生效",
                "label": copy.deepcopy(label), "rule": self._extract_rule(label)}

    # ── 聚类作业轮次 ──────────────────────────────────────────────
    def start_cluster_round(
        self,
        rule_version_id: int | None = None,
        sample_ids: list[str] | None = None,
        *,
        rule_version: str | None = None,
        cluster_config_id: int | None = None,
        operator: str = "聚类作业",
    ) -> dict[str, Any]:
        """以选定的已生效规则开启一轮聚类，对实际样本写入待处理场景标签。

        一轮只锁定一版规则；规则必须完整。开跑时把样本当时的字段取值和字段版本一起
        快照，后续样本维表变化不会影响这一轮的结果和重放。
        """
        version = self._resolve_version(rule_version_id=rule_version_id, rule_version=rule_version)
        if version is None:
            return {"ok": False, "message": "未找到指定的已生效规则版本"}
        if version.get("status") != EFFECTIVE:
            return {"ok": False, "message": f"规则 {version.get('rule_version')} 当前不是已生效版本，不能开启新轮次"}
        missing = self._missing_rule_parts(version)
        if missing:
            return {"ok": False, "message": f"规则版本不完整，缺少：{'、'.join(missing)}"}

        samples = self._snapshot_samples(sample_ids)
        if not samples:
            return {"ok": False, "message": "没有可用的实际样本"}
        if cluster_config_id is not None and store.find("cluster_config", cluster_config_id) is None:
            return {"ok": False, "message": f"聚类配置 {cluster_config_id} 不存在"}

        round_id = store.next_id(ROUND_TABLE)
        labels = []
        label_store = store.internal(LABEL_TABLE)
        for sample in samples:
            classification = self._classify(sample, version)
            label_store.append({
                "id": store.next_id(LABEL_TABLE),
                "round_id": round_id,
                "sample_id": sample["sample_id"],
                "样本快照": copy.deepcopy(sample),
                "场景标签": classification,
                "分类结果": classification,
                "rule_version": version["rule_version"],
                "rule_version_id": version["id"],
                "状态": ROUND_PENDING,
                "pending": True,
            })
            labels.append(label_store[-1])

        record = {
            "id": round_id,
            "轮次编号": f"CLUSTER-R{round_id:04d}",
            "cluster_config_id": cluster_config_id,
            "rule_version_id": version["id"],
            "rule_version": version["rule_version"],
            "规则快照": copy.deepcopy(version),
            "样本快照": samples,
            "样本名单": [sample["sample_id"] for sample in samples],
            "标签": [self._label_view(label) for label in labels],
            "状态": ROUND_PENDING,
            "pending": True,
            "abnormal": False,
            "开始时间": "2026-11-22",
            "完成时间": None,
            "归档时间": None,
            "操作人": operator,
        }
        store.internal(ROUND_TABLE).append(record)
        return {
            "ok": True,
            "message": f"轮次 {record['轮次编号']} 已按规则 {version['rule_version']} 开跑，标签待处理",
            "round": self._round_view(record),
            "receipt": self._build_receipt(record),
        }

    def apply_latest_rule(self, round_id: int) -> dict[str, Any]:
        """把当前最新生效规则应用到尚未提交的轮次，并重算整轮样本标签。

        开跑后若发布了新版规则，旧轮次保留开跑时的规则继续处理；只有显式执行本动作
        才会换成新版，而且必须整轮重算，不能只改部分样本。
        """
        record = self._find_round(round_id)
        if record is None:
            return {"ok": False, "message": f"聚类轮次 {round_id} 不存在"}
        if record["状态"] in (ROUND_EFFECTIVE, ROUND_ARCHIVED, ROUND_INVALIDATED):
            return {"ok": False, "message": f"轮次 {record['轮次编号']} 已{record['状态']}，不允许改用新版规则"}
        latest = self._latest_effective_version()
        if latest is None:
            return {"ok": False, "message": "当前没有可应用的已生效规则版本"}
        if latest["id"] == record["rule_version_id"]:
            return {"ok": False, "message": f"轮次已采用最新规则 {latest['rule_version']}，无需重算"}

        before = {label["sample_id"]: label["分类结果"] for label in record["标签"]}
        record["rule_version_id"] = latest["id"]
        record["rule_version"] = latest["rule_version"]
        record["规则快照"] = copy.deepcopy(latest)
        record["标签"] = []
        record["样本名单"] = []
        label_store = store.internal(LABEL_TABLE)
        label_store[:] = [label for label in label_store if label.get("round_id") != round_id]
        for sample in record["样本快照"]:
            classification = self._classify(sample, latest)
            label_store.append({
                "id": store.next_id(LABEL_TABLE),
                "round_id": round_id,
                "sample_id": sample["sample_id"],
                "样本快照": copy.deepcopy(sample),
                "场景标签": classification,
                "分类结果": classification,
                "rule_version": latest["rule_version"],
                "rule_version_id": latest["id"],
                "状态": ROUND_PENDING,
                "pending": True,
            })
            record["标签"].append(self._label_view(label_store[-1]))
            record["样本名单"].append(sample["sample_id"])
        after = {label["sample_id"]: label["分类结果"] for label in record["标签"]}
        conflicts = [
            {"sample_id": sample_id, "旧分类": before[sample_id], "新分类": after[sample_id]}
            for sample_id in before if before[sample_id] != after.get(sample_id)
        ]
        return {
            "ok": True,
            "message": f"轮次 {record['轮次编号']} 已改用规则 {latest['rule_version']} 并重算整轮",
            "round": self._round_view(record),
            "rule_version": latest["rule_version"],
            "recomputed": len(record["标签"]),
            "changed_samples": conflicts,
            "receipt": self._build_receipt(record),
            "实际名单": [label.get("sample_id") for label in record["标签"]
                       if label.get("分类结果") != UNMATCHED],
        }

    def submit_round(self, round_id: int) -> dict[str, Any]:
        """提交一轮聚类结果：校验整轮只采用一版完整规则，标签对外生效。"""
        record = self._find_round(round_id)
        if record is None:
            return {"ok": False, "message": f"聚类轮次 {round_id} 不存在"}
        if record["状态"] != ROUND_PENDING:
            return {"ok": False, "message": f"轮次 {record['轮次编号']} 当前状态为{record['状态']}，不能重复提交"}
        versions = {label.get("rule_version") for label in record["标签"]}
        if versions != {record["rule_version"]}:
            return {"ok": False, "message": f"轮次标签引用了多个规则版本：{sorted(v for v in versions if v)}"}
        record["状态"] = ROUND_EFFECTIVE
        record["pending"] = False
        record["完成时间"] = "2026-11-22"
        label_store = store.internal(LABEL_TABLE)
        for label in label_store:
            if label.get("round_id") == round_id:
                label["状态"] = ROUND_EFFECTIVE
                label["pending"] = False
        for label in record["标签"]:
            label["状态"] = ROUND_EFFECTIVE
            label["pending"] = False
        return {
            "ok": True,
            "message": f"轮次 {record['轮次编号']} 已提交生效，采用规则 {record['rule_version']}",
            "round": self._round_view(record),
            "receipt": self._build_receipt(record),
        }

    def archive_round(self, round_id: int) -> dict[str, Any]:
        """归档已生效轮次，保留规则与样本快照供历史查询和重放。"""
        record = self._find_round(round_id)
        if record is None:
            return {"ok": False, "message": f"聚类轮次 {round_id} 不存在"}
        if record["状态"] == ROUND_ARCHIVED:
            return {"ok": False, "message": f"轮次 {record['轮次编号']} 已归档"}
        if record["状态"] != ROUND_EFFECTIVE:
            return {"ok": False, "message": f"轮次 {record['轮次编号']} 尚未生效，不能归档"}
        record["状态"] = ROUND_ARCHIVED
        record["pending"] = False
        record["归档时间"] = "2026-11-22"
        label_store = store.internal(LABEL_TABLE)
        for label in label_store:
            if label.get("round_id") == round_id:
                label["状态"] = ROUND_ARCHIVED
                label["pending"] = False
                label["archived"] = True
        for label in record["标签"]:
            label["状态"] = ROUND_ARCHIVED
            label["pending"] = False
            label["archived"] = True
        return {
            "ok": True,
            "message": f"轮次 {record['轮次编号']} 的 {len(record['标签'])} 条标签已从待处理流转为已归档",
            "round": self._round_view(record),
            "receipt": self._build_receipt(record),
        }

    def list_rounds(self, status: str | None = None) -> dict[str, Any]:
        """查询聚类历史：每轮的规则版本、样本名单、状态和回执摘要。"""
        rounds = [self._round_view(record) for record in store.internal(ROUND_TABLE)]
        if status:
            rounds = [record for record in rounds if record["状态"] == status]
        return {"ok": True, "items": rounds, "total": len(rounds)}

    def get_round(self, round_id: int) -> dict[str, Any]:
        record = self._find_round(round_id)
        if record is None:
            return {"ok": False, "message": f"聚类轮次 {round_id} 不存在"}
        return {
            "ok": True,
            "round": self._round_view(record),
            "labels": [self._label_view(label) for label in record["标签"]],
            "receipt": self._build_receipt(record),
        }

    def get_receipt(self, round_id: int) -> dict[str, Any]:
        """运行回执：采用的规则版本、每个样本的分类结果。"""
        record = self._find_round(round_id)
        if record is None:
            return {"ok": False, "message": f"聚类轮次 {round_id} 不存在"}
        return {"ok": True, "receipt": self._build_receipt(record)}

    # ── 重放 ──────────────────────────────────────────────────────
    def replay_round(self, round_id: int, archive: dict[str, Any] | None = None) -> dict[str, Any]:
        """用归档规则和归档样本重算名单，输出与归档名单的差异。

        ``archive`` 可传入一份外部历史归档（规则/样本/标签）。无论当前样本维表或规则
        来源改成什么样，重算只读归档快照；重放也不会改动原轮次和原归档。
        """
        record = self._find_round(round_id)
        if record is None:
            return {"ok": False, "message": f"聚类轮次 {round_id} 不存在"}
        source = copy.deepcopy(archive) if archive else self._archive_from_round(record)
        version = source.get("规则") or source.get("规则快照")
        samples = source.get("样本") or source.get("样本快照") or []
        archived_labels = source.get("标签") or []
        if not version or not samples:
            return {"ok": False, "message": "归档缺少规则或样本，无法重放"}

        computed = []
        for sample in samples:
            computed.append({
                "sample_id": sample.get("sample_id") or sample.get("样本编号"),
                "样本快照": copy.deepcopy(sample),
                "分类结果": self._classify(sample, version),
            })
        computed_ids = [item["sample_id"] for item in computed]
        archived_ids = [
            item.get("sample_id") or item.get("样本编号")
            for item in archived_labels
        ]
        computed_set, archived_set = set(computed_ids), set(archived_ids)
        missing = [sample_id for sample_id in computed_ids if sample_id not in archived_set]
        extra = [sample_id for sample_id in archived_ids if sample_id not in computed_set]
        changed = []
        archived_classification = {
            (item.get("sample_id") or item.get("样本编号")): item.get("分类结果") or item.get("场景标签")
            for item in archived_labels
        }
        for item in computed:
            sample_id = item["sample_id"]
            if sample_id in archived_classification and archived_classification[sample_id] != item["分类结果"]:
                changed.append({
                    "sample_id": sample_id,
                    "归档分类": archived_classification[sample_id],
                    "重算分类": item["分类结果"],
                })
        diff = {
            "计算名单": computed_ids,
            "归档名单": archived_ids,
            "漏标样本": missing,
            "多出样本": extra,
            "分类变化": changed,
            "数量差异": len(computed_ids) - len(archived_ids),
            "是否一致": not missing and not extra and not changed,
        }
        replay_id = store.next_id(REPLAY_TABLE)
        store.internal(REPLAY_TABLE).append({
            "id": replay_id,
            "round_id": round_id,
            "rule_version": version.get("rule_version"),
            "计算名单": computed_ids,
            "归档名单": archived_ids,
            "漏标样本": missing,
            "多出样本": extra,
            "分类变化": changed,
            "来源": "外部历史归档" if archive else "轮次归档快照",
        })
        return {
            "ok": True,
            "replay_id": replay_id,
            "round_id": round_id,
            "round_no": record["轮次编号"],
            "rule_version": version.get("rule_version"),
            "computed_list": computed,
            "computed_ids": computed_ids,
            "archived_list": [copy.deepcopy(item) for item in archived_labels],
            "archived_ids": archived_ids,
            "diff": diff,
            "original_unchanged": True,
            "message": f"重放完成：计算 {len(computed_ids)} 人、归档 {len(archived_ids)} 人，差异 {len(missing) + len(extra)} 人",
        }

    def list_replays(self, round_id: int | None = None) -> dict[str, Any]:
        replays = [copy.deepcopy(item) for item in store.internal(REPLAY_TABLE)
                   if round_id is None or item.get("round_id") == round_id]
        return {"ok": True, "items": replays, "total": len(replays)}

    # ── 样本维表 ──────────────────────────────────────────────────
    def list_samples(self) -> dict[str, Any]:
        samples = copy.deepcopy(store.internal(SAMPLE_TABLE))
        return {"ok": True, "items": samples, "total": len(samples)}

    def update_sample(self, sample_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """更新当前样本维表（不影响任何轮次的样本快照）。"""
        for sample in store.internal(SAMPLE_TABLE):
            if sample.get("sample_id") == sample_id:
                sample.update(copy.deepcopy(values))
                return {"ok": True, "message": f"样本 {sample_id} 已更新", "sample": copy.deepcopy(sample)}
        return {"ok": False, "message": f"样本 {sample_id} 不存在"}

    # ── 内部工具 ──────────────────────────────────────────────────
    def _is_draft_label(self, label: dict[str, Any]) -> bool:
        return label.get("status") in (DRAFT, "待校验")

    def _effective_view(self, version: dict[str, Any]) -> dict[str, Any]:
        """规则版本的对外视图：版本号、筛选条件、字段版本和完整性都平铺出来。"""
        return {
            "id": version.get("id"),
            "版本编号": version.get("版本编号"),
            "rule_version": version.get("rule_version"),
            "来源标注编号": version.get("来源标注编号"),
            "场景名称": version.get("场景名称"),
            "规则编号": version.get("规则编号"),
            "筛选条件": copy.deepcopy(version.get("筛选条件")),
            "字段版本": copy.deepcopy(version.get("字段版本")),
            "分群标签": version.get("分群标签"),
            "规则完整性": version.get("规则完整性"),
            "status": version.get("status"),
            "发布人": version.get("发布人"),
            "发布时间": version.get("发布时间"),
            "历史归档时间": version.get("历史归档时间"),
        }

    def _draft_view(self, label: dict[str, Any]) -> dict[str, Any]:
        rule = self._extract_rule(label)
        return {
            "id": label.get("id"),
            "标注编号": label.get("标注编号"),
            "场景名称": label.get("场景名称"),
            "关联特征": label.get("关联特征"),
            "业务规则": label.get("业务规则"),
            "筛选条件": copy.deepcopy(rule["condition"]),
            "字段版本": copy.deepcopy(rule["field_versions"]),
            "分群标签": rule["segment_label"],
            "status": DRAFT,
            "缺失部分": self._missing_rule_parts(rule),
        }

    def _extract_rule(self, label: dict[str, Any]) -> dict[str, Any]:
        condition = label.get("筛选条件")
        if not isinstance(condition, dict) or not condition:
            condition = self._parse_rule_text(label.get("业务规则"))
        field_versions = label.get("字段版本")
        if not isinstance(field_versions, dict):
            field_versions = {}
        segment_label = label.get("分群标签") or label.get("场景名称") or "目标分群"
        return {
            "condition": copy.deepcopy(condition),
            "field_versions": copy.deepcopy(field_versions),
            "segment_label": str(segment_label),
        }

    def _parse_rule_text(self, raw: Any) -> dict[str, Any]:
        """把「年龄 >= 18」这类规则文本解析成结构化条件，解析不了就返回空条件。"""
        text = str(raw or "").strip()
        for operator in (">=", "<=", ">", "<", "==", "="):
            if operator in text:
                field, _, value = text.partition(operator)
                field = field.strip()
                value = value.strip()
                if not field or not value:
                    continue
                if operator == "=":
                    operator = "=="
                parsed_value: Any = value
                try:
                    parsed_value = float(value)
                    if parsed_value.is_integer():
                        parsed_value = int(parsed_value)
                except ValueError:
                    pass
                return {"字段": field, "运算符": operator, "值": parsed_value}
        return {}

    def _missing_rule_parts(self, rule: dict[str, Any]) -> list[str]:
        condition = rule.get("condition") or rule.get("筛选条件") or {}
        field_versions = rule.get("field_versions") or rule.get("字段版本") or {}
        segment_label = (rule.get("segment_label") or rule.get("分群标签")
                         or rule.get("命中分群"))
        missing = []
        if not condition.get("字段") or not condition.get("运算符") or "值" not in condition:
            missing.append("筛选条件")
        elif not field_versions.get(str(condition.get("字段"))):
            missing.append("筛选字段的字段版本")
        if not segment_label:
            missing.append("分群标签")
        return missing

    def _resolve_version(
        self,
        *,
        rule_version_id: int | None = None,
        rule_version: str | None = None,
    ) -> dict[str, Any] | None:
        versions = store.internal(RULE_TABLE)
        if rule_version_id is not None:
            for version in versions:
                if int(version.get("id", 0)) == int(rule_version_id):
                    return version
            return None
        if rule_version:
            for version in versions:
                if version.get("rule_version") == rule_version:
                    return version
            return None
        return self._latest_effective_version()

    def _latest_effective_version(self) -> dict[str, Any] | None:
        effective = [v for v in store.internal(RULE_TABLE) if v.get("status") == EFFECTIVE]
        if not effective:
            return None
        return max(effective, key=lambda v: int(v.get("id", 0)))

    def _snapshot_samples(self, sample_ids: list[str] | None) -> list[dict[str, Any]]:
        samples = store.internal(SAMPLE_TABLE)
        selected = []
        for sample in samples:
            if sample_ids is not None and sample.get("sample_id") not in sample_ids:
                continue
            snapshot = copy.deepcopy(sample)
            snapshot.setdefault("field_versions", copy.deepcopy(snapshot.get("字段版本", {})))
            selected.append(snapshot)
        return selected

    def _classify(self, sample: dict[str, Any], version: dict[str, Any]) -> str:
        """按规则版本的筛选条件给样样本打分类；命中返回分群标签，否则未命中分群。"""
        rule = version.get("快照") or version
        condition = version.get("筛选条件") or rule.get("condition") or {}
        segment = (version.get("分群标签") or version.get("命中分群")
                   or rule.get("segment_label") or "目标分群")
        field = str(condition.get("字段") or "")
        operator = str(condition.get("运算符") or ">=")
        expected = condition.get("值")
        actual = sample.get(field)
        if actual is None:
            actual = (sample.get("值") or {}).get(field) if isinstance(sample.get("值"), dict) else None
        if field and self._compare(actual, operator, expected):
            return str(segment)
        return UNMATCHED

    @staticmethod
    def _compare(actual: Any, operator: str, expected: Any) -> bool:
        if actual is None:
            return False
        try:
            left, right = float(actual), float(expected)
        except (TypeError, ValueError):
            left, right = str(actual), str(expected)  # type: ignore[assignment]
        if operator == ">=":
            return left >= right
        if operator == "<=":
            return left <= right
        if operator == ">":
            return left > right
        if operator == "<":
            return left < right
        if operator in ("==", "="):
            return left == right
        if operator == "!=":
            return left != right
        return False

    def _find_round(self, round_id: int) -> dict[str, Any] | None:
        for record in store.internal(ROUND_TABLE):
            if int(record.get("id", 0)) == int(round_id):
                return record
        return None

    def _archive_from_round(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "规则": copy.deepcopy(record.get("规则快照")),
            "样本": copy.deepcopy(record.get("样本快照")),
            "标签": copy.deepcopy(record.get("标签")),
        }

    def _build_receipt(self, record: dict[str, Any]) -> dict[str, Any]:
        classifications = [
            {
                "sample_id": label.get("sample_id"),
                "年龄": label.get("样本快照", {}).get("年龄"),
                "分类结果": label.get("分类结果"),
                "场景标签": label.get("场景标签"),
                "rule_version": label.get("rule_version"),
            }
            for label in record.get("标签", [])
        ]
        age_20 = next((item for item in classifications
                       if item.get("年龄") == 20 or item.get("sample_id") == "S-020"), None)
        return {
            "轮次编号": record.get("轮次编号"),
            "rule_version": record.get("rule_version"),
            "rule_version_id": record.get("rule_version_id"),
            "状态": record.get("状态"),
            "样本数量": len(classifications),
            "分类结果": classifications,
            "20岁样本分类结果": age_20,
            "样本名单": record.get("样本名单", []),
        }

    def _label_view(self, label: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": label.get("id"),
            "round_id": label.get("round_id"),
            "sample_id": label.get("sample_id"),
            "样本快照": copy.deepcopy(label.get("样本快照")),
            "分类结果": label.get("分类结果"),
            "场景标签": label.get("场景标签"),
            "rule_version": label.get("rule_version"),
            "状态": label.get("状态"),
            "pending": label.get("pending", False),
        }

    def _round_view(self, record: dict[str, Any]) -> dict[str, Any]:
        labels = record.get("标签", [])
        hit_ids = [label.get("sample_id") for label in labels
                   if label.get("分类结果") != UNMATCHED]
        return {
            "id": record.get("id"),
            "轮次编号": record.get("轮次编号"),
            "cluster_config_id": record.get("cluster_config_id"),
            "rule_version": record.get("rule_version"),
            "rule_version_id": record.get("rule_version_id"),
            "规则": copy.deepcopy(record.get("规则快照")),
            "规则完整性": (record.get("规则快照") or {}).get("规则完整性", "完整"),
            "样本名单": list(record.get("样本名单", [])),
            "样本数量": len(record.get("样本名单", [])),
            "实际名单": hit_ids,
            "命中名单": hit_ids,
            "标签": [self._label_view(label) for label in labels],
            "状态": record.get("状态"),
            "pending": record.get("pending", False),
            "开始时间": record.get("开始时间"),
            "完成时间": record.get("完成时间"),
            "归档时间": record.get("归档时间"),
            "操作人": record.get("操作人"),
        }
