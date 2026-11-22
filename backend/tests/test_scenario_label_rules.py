"""场景标注规则发布 / 聚类轮次 / 重放 的链路测试。

按运营实际走一遍：先按最低年龄 18 归档一轮，再开一轮时发布下限 23，
验证轮次锁定旧版、应用新版必须整轮重算，最后用归档和缺记录的历史归档做重放。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.seed import SEED_INTERNAL, SEED_ROWS
from app.services import service_for
from app.store import store


def _svc():
    return service_for("scenario_label")


def _publish_age_rule(age: int, field_version: str, version: str):
    svc = _svc()
    return svc.publish_rule_version(
        1,
        rule_version=version,
        condition={"字段": "年龄", "运算符": ">=", "值": age},
        field_versions={"年龄": field_version},
        segment_label="成年客群",
    )


def _reset_internal():
    for table in ("scenario_rule_versions", "scenario_cluster_rounds",
                  "scenario_sample_labels", "scenario_replays",
                  "scenario_samples"):
        store.internal(table).clear()
    # 恢复标注和样本种子行，避免前一个用例改过状态、取值影响后续断言
    store.rows("scenario_label")[:] = [dict(row) for row in SEED_ROWS["scenario_label"]]
    store.internal("scenario_samples").extend(
        [dict(row) for row in SEED_INTERNAL["scenario_samples"]]
    )


def test_publish_keeps_condition_and_field_version():
    _reset_internal()
    svc = _svc()
    first = _publish_age_rule(18, "age_v1", "v18")
    assert first["ok"], first
    assert first["version"]["筛选条件"]["值"] == 18
    assert first["version"]["字段版本"] == {"年龄": "age_v1"}
    assert first["version"]["status"] == "已生效"

    second = _publish_age_rule(23, "age_v2", "v23")
    assert second["ok"], second
    groups = svc.list_rules()
    assert [v["rule_version"] for v in groups["groups"]["已生效"]] == ["v23"]
    historical = groups["groups"]["历史归档"]
    assert [v["rule_version"] for v in historical] == ["v18"]
    # 历史归档仍保留当时的下限和字段版本
    assert historical[0]["筛选条件"]["值"] == 18
    assert historical[0]["字段版本"] == {"年龄": "age_v1"}


def test_draft_publish_and_run_flow():
    _reset_internal()
    svc = _svc()
    draft = svc.list_rules()["groups"]["草稿"]
    assert any(item["标注编号"] == "SCEN-0001" for item in draft)

    published = _publish_age_rule(18, "age_v1", "v18")
    version_id = published["version_id"]

    # 第一轮：两位样本都满 18
    r1 = svc.start_cluster_round(version_id, ["S-020", "S-025"], cluster_config_id=3)
    assert r1["ok"], r1
    round1 = r1["round"]["id"]
    assert r1["round"]["rule_version"] == "v18"
    assert {label["分类结果"] for label in r1["round"]["标签"]} == {"成年客群"}
    assert r1["round"]["状态"] == "待处理"

    submitted = svc.submit_round(round1)
    assert submitted["ok"], submitted
    archived = svc.archive_round(round1)
    assert archived["ok"], archived
    assert archived["round"]["状态"] == "已归档"
    assert all(label["状态"] == "已归档" for label in archived["round"]["标签"])

    # 第二轮开跑时仍是 v18
    r2 = svc.start_cluster_round(version_id, ["S-020", "S-025"], cluster_config_id=3)
    assert r2["ok"], r2
    round2 = r2["round"]["id"]

    # 20 岁样本已暂算，但轮次尚未对外生效时发布下限 23
    _publish_age_rule(23, "age_v2", "v23")
    readback = svc.get_round(round2)
    assert readback["round"]["rule_version"] == "v18"
    assert readback["round"]["样本名单"] == ["S-020", "S-025"]
    assert {label["分类结果"] for label in readback["labels"]} == {"成年客群"}

    # 应用新版：整轮重算，只剩 25 岁命中
    applied = svc.apply_latest_rule(round2)
    assert applied["ok"], applied
    assert applied["round"]["rule_version"] == "v23"
    assert applied["recomputed"] == 2
    results = {label["sample_id"]: label["分类结果"] for label in applied["round"]["标签"]}
    assert results == {"S-020": "未命中分群", "S-025": "成年客群"}
    assert applied["changed_samples"] == [
        {"sample_id": "S-020", "旧分类": "成年客群", "新分类": "未命中分群"}
    ]
    assert applied["receipt"]["rule_version"] == "v23"
    assert applied["receipt"]["20岁样本分类结果"]["分类结果"] == "未命中分群"

    # 轮次 1 的归档结果不能被第二轮或新版本覆盖
    archived_round1 = svc.get_round(round1)
    assert archived_round1["round"]["rule_version"] == "v18"
    assert {label["分类结果"] for label in archived_round1["labels"]} == {"成年客群"}

    submitted2 = svc.submit_round(round2)
    assert submitted2["ok"], submitted2
    assert svc.archive_round(round2)["ok"]

    # 第三轮：只给 25 岁打标签，待处理 → 已生效 → 已归档
    r3 = svc.start_cluster_round(rule_version_id=applied["round"]["rule_version_id"], sample_ids=["S-025"])
    assert r3["ok"], r3
    assert r3["round"]["样本名单"] == ["S-025"]
    assert r3["round"]["标签"][0]["状态"] == "待处理"
    assert svc.submit_round(r3["round"]["id"])["ok"]
    done = svc.archive_round(r3["round"]["id"])
    assert done["round"]["标签"][0]["状态"] == "已归档"
    assert done["round"]["标签"][0]["pending"] is False


def test_round_locked_rule_keeps_processing_after_publish():
    """开跑后发布新版：旧轮次仍可用开跑时的规则继续处理并提交。"""
    _reset_internal()
    svc = _svc()
    v18 = _publish_age_rule(18, "age_v1", "v18")
    started = svc.start_cluster_round(v18["version_id"], ["S-020", "S-025"])
    round_id = started["round"]["id"]

    _publish_age_rule(23, "age_v2", "v23")

    # 不应用新版，直接提交：整轮仍是 v18，两人都命中
    submitted = svc.submit_round(round_id)
    assert submitted["ok"], submitted
    assert submitted["round"]["rule_version"] == "v18"
    assert {label["rule_version"] for label in submitted["round"]["标签"]} == {"v18"}
    assert {label["分类结果"] for label in submitted["round"]["标签"]} == {"成年客群"}
    assert submitted["receipt"]["rule_version"] == "v18"

    # 历史版本不能用来开新轮次
    rejected = svc.start_cluster_round(v18["version_id"], ["S-025"])
    assert not rejected["ok"]


def test_incomplete_rule_cannot_start_round():
    _reset_internal()
    svc = _svc()
    # SCEN-0002 没有字段版本，发布应被拒绝
    incomplete = svc.publish_rule_version(
        2,
        condition={"字段": "年龄", "运算符": ">=", "值": 30},
        field_versions={},
    )
    assert not incomplete["ok"]
    assert "字段版本" in incomplete["message"]


def test_replay_uses_archive_and_reports_diff():
    _reset_internal()
    svc = _svc()
    published = _publish_age_rule(18, "age_v1", "v18")
    started = svc.start_cluster_round(published["version_id"], ["S-020", "S-025"])
    round_id = started["round"]["id"]
    assert svc.submit_round(round_id)["ok"]
    assert svc.archive_round(round_id)["ok"]
    original = svc.get_round(round_id)

    # 当前样本维表的字段和值都被改过，规则来源也被改成下限 1
    svc.update_sample("S-020", {"年龄": 99, "字段版本": {"年龄": "age_v9"}})
    svc.update_sample("S-025", {"年龄": 66, "字段版本": {"年龄": "age_v9"}})
    svc.update_rule_source(1, condition={"字段": "年龄", "运算符": ">=", "值": 1},
                           field_versions={"年龄": "age_v9"})

    replay = svc.replay_round(round_id)
    assert replay["ok"], replay
    assert replay["rule_version"] == "v18"
    assert replay["computed_ids"] == ["S-020", "S-025"]
    assert replay["archived_ids"] == ["S-020", "S-025"]
    assert replay["diff"]["漏标样本"] == []
    assert replay["diff"]["多出样本"] == []
    assert replay["diff"]["分类变化"] == []
    assert replay["diff"]["是否一致"] is True
    # 原结果保持不变
    after = svc.get_round(round_id)
    assert after["labels"] == original["labels"]

    # 外部历史归档漏掉 20 岁样本（直接取内部归档快照构造）
    record = next(r for r in store.internal("scenario_cluster_rounds") if r["id"] == round_id)
    external = {
        "规则": record["规则快照"],
        "样本": record["样本快照"],
        "标签": [label for label in record["标签"] if label["sample_id"] != "S-020"],
    }
    partial = svc.replay_round(round_id, archive=external)
    assert partial["ok"], partial
    assert partial["computed_ids"] == ["S-020", "S-025"]
    assert partial["archived_ids"] == ["S-025"]
    assert partial["diff"]["漏标样本"] == ["S-020"]
    assert partial["diff"]["是否一致"] is False
    # 原归档仍是两条
    assert len(record["标签"]) == 2


if __name__ == "__main__":
    tests = [
        test_publish_keeps_condition_and_field_version,
        test_draft_publish_and_run_flow,
        test_round_locked_rule_keeps_processing_after_publish,
        test_incomplete_rule_cannot_start_round,
        test_replay_uses_archive_and_reports_diff,
    ]
    passed = failed = 0
    for test in tests:
        try:
            test()
            print(f"[PASS] {test.__name__}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {test.__name__}: {exc}")
            failed += 1
    print(f"\n总测试: {len(tests)}, 通过: {passed}, 失败: {failed}")
    if failed:
        sys.exit(1)
