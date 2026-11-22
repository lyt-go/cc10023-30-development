/** 场景标注规则发布 / 聚类轮次 / 重放的接口封装。 */
import { request } from './client'

export type RuleCondition = {
  字段: string
  运算符: string
  值: string | number
}

export type RuleVersion = {
  id: number
  版本编号: string
  rule_version: string
  来源标注编号: string
  场景名称: string
  规则编号: string
  筛选条件: RuleCondition | null
  字段版本: Record<string, string>
  分群标签: string
  规则完整性: string
  status: string
  发布人: string
  发布时间: string
  历史归档时间: string | null
}

export type RuleDraft = {
  id: number
  标注编号: string
  场景名称: string
  关联特征: string
  业务规则: string
  筛选条件: RuleCondition | null
  字段版本: Record<string, string>
  分群标签: string
  status: string
  缺失部分: string[]
}

export type LabelItem = {
  id: number
  round_id: number
  sample_id: string
  样本快照: Record<string, unknown>
  分类结果: string
  场景标签: string
  rule_version: string
  状态: string
  pending: boolean
}

export type RoundItem = {
  id: number
  轮次编号: string
  cluster_config_id: number | null
  rule_version: string
  rule_version_id: number
  规则完整性: string
  样本名单: string[]
  样本数量: number
  实际名单: string[]
  标签: LabelItem[]
  状态: string
  pending: boolean
  开始时间: string
  完成时间: string | null
  归档时间: string | null
  操作人: string
}

export type Receipt = {
  轮次编号: string
  rule_version: string
  rule_version_id: number
  状态: string
  样本数量: number
  分类结果: {
    sample_id: string
    年龄: number | null
    分类结果: string
    场景标签: string
    rule_version: string
  }[]
  '20岁样本分类结果': {
    sample_id: string
    年龄: number | null
    分类结果: string
    场景标签: string
    rule_version: string
  } | null
  样本名单: string[]
}

export type ReplayDiff = {
  计算名单: string[]
  归档名单: string[]
  漏标样本: string[]
  多出样本: string[]
  分类变化: { sample_id: string; 归档分类: string; 重算分类: string }[]
  数量差异: number
  是否一致: boolean
}

export type ReplayResult = {
  ok: boolean
  replay_id: number
  round_id: number
  round_no: string
  rule_version: string
  computed_ids: string[]
  archived_ids: string[]
  diff: ReplayDiff
  original_unchanged: boolean
  message: string
}

export type SampleItem = {
  id: number
  sample_id: string
  客户编号: string
  年龄: number
  城市: string
  字段版本: Record<string, string>
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

const BASE = '/api/scenario_label'

export async function loadRuleGroups(): Promise<{
  groups: { 草稿: RuleDraft[]; 已生效: RuleVersion[]; 历史归档: RuleVersion[] }
}> {
  return await json(`${BASE}/rules`)
}

export async function loadSamples(): Promise<{ items: SampleItem[] }> {
  return await json(`${BASE}/samples`)
}

export async function publishRule(payload: {
  label_id: number
  rule_version?: string
  condition: RuleCondition
  field_versions: Record<string, string>
  segment_label?: string
}): Promise<{ ok: boolean; message: string; rule_version?: string }> {
  return await json(`${BASE}/rules/publish`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function saveDraft(
  labelId: number,
  payload: {
    condition: RuleCondition
    field_versions: Record<string, string>
    segment_label?: string
  },
): Promise<{ ok: boolean; message: string }> {
  return await json(`${BASE}/rules/drafts/${labelId}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function loadRounds(): Promise<{ items: RoundItem[]; total: number }> {
  return await json(`${BASE}/rounds`)
}

export async function loadRound(
  roundId: number,
): Promise<{ ok: boolean; round: RoundItem; labels: LabelItem[]; receipt: Receipt }> {
  return await json(`${BASE}/rounds/${roundId}`)
}

export async function startRound(payload: {
  rule_version: string
  sample_ids: string[]
  cluster_config_id?: number
}): Promise<{ ok: boolean; message: string; round?: RoundItem; receipt?: Receipt }> {
  return await json(`${BASE}/rounds`, { method: 'POST', body: JSON.stringify(payload) })
}

export async function runRoundAction(
  roundId: number,
  action: string,
): Promise<{
  ok: boolean
  message: string
  round?: RoundItem
  receipt?: Receipt
  changed_samples?: { sample_id: string; 旧分类: string; 新分类: string }[]
  实际名单?: string[]
}> {
  return await json(`${BASE}/rounds/${roundId}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
}

export async function loadReceipt(roundId: number): Promise<{ ok: boolean; receipt: Receipt }> {
  return await json(`${BASE}/rounds/${roundId}/receipt`)
}

export async function replayRound(
  roundId: number,
  archive?: Record<string, unknown>,
): Promise<ReplayResult> {
  return await json(`${BASE}/rounds/${roundId}/replay`, {
    method: 'POST',
    body: JSON.stringify(archive ? { archive } : {}),
  })
}
