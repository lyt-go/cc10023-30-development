<template>
  <section class="page" data-module="scenario_label_rules">
    <header class="page-head">
      <div>
        <h2>场景标注 · 分群规则</h2>
        <p class="page-desc">
          从场景标注规则发布已生效规则版本，锁定当时的筛选条件和字段版本，供聚类分群作业按版本领取。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reloadAll">刷新数据</button>
      </div>
    </header>

    <p v-if="message" class="notice" :class="{ 'notice-error': messageError }">{{ message }}</p>

    <div class="stat-row">
      <article v-for="card in cards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>

    <!-- 规则发布 -->
    <div class="panel">
      <div class="panel-head">
        <h3>规则发布</h3>
        <span class="panel-hint">左：草稿（可编辑）；右：已生效 / 历史归档（只读快照）</span>
      </div>
      <div class="panel-grid">
        <div class="panel-col">
          <h4>草稿</h4>
          <table class="data-table">
            <thead>
              <tr><th>标注编号</th><th>场景名称</th><th>分群标签</th><th>筛选条件</th><th>字段版本</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="draft in rules.草稿" :key="draft.id" :data-testid="`draft-${draft.id}`">
                <td>{{ draft.标注编号 }}</td>
                <td>{{ draft.场景名称 }}</td>
                <td>{{ draft.分群标签 }}</td>
                <td>
                  <input v-model="editState[draft.id].field" class="mini" placeholder="字段" />
                  <select v-model="editState[draft.id].operator" class="mini">
                    <option v-for="op in OPERATORS" :key="op" :value="op">{{ op }}</option>
                  </select>
                  <input v-model="editState[draft.id].value" class="mini" placeholder="值" />
                </td>
                <td>
                  <input v-model="editState[draft.id].fieldVersion" class="mini" placeholder="字段版本" />
                </td>
                <td class="row-actions">
                  <button class="link" type="button" :data-testid="`save-draft-${draft.id}`" @click="saveRuleDraft(draft)">
                    保存草稿
                  </button>
                  <button class="link" type="button" :data-testid="`publish-${draft.id}`" @click="publishDraft(draft)">
                    发布生效
                  </button>
                </td>
              </tr>
              <tr v-if="!rules.草稿.length">
                <td colspan="6" class="empty-state">没有待发布的规则草稿</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="panel-col">
          <h4>已生效版本</h4>
          <table class="data-table">
            <thead>
              <tr><th>版本</th><th>来源标注</th><th>筛选条件</th><th>字段版本</th><th>完整性</th><th>发布时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="version in rules.已生效" :key="version.id" :data-testid="`effective-${version.id}`">
                <td>{{ version.rule_version }}</td>
                <td>{{ version.来源标注编号 }}</td>
                <td>{{ conditionText(version.筛选条件) }}</td>
                <td>{{ fieldVersionText(version.字段版本) }}</td>
                <td>{{ version.规则完整性 }}</td>
                <td>{{ version.发布时间 }}</td>
              </tr>
              <tr v-if="!rules.已生效.length">
                <td colspan="6" class="empty-state">还没有已生效规则版本</td>
              </tr>
            </tbody>
          </table>

          <h4>历史归档版本</h4>
          <table class="data-table">
            <thead>
              <tr><th>版本</th><th>筛选条件</th><th>字段版本</th><th>归档时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="version in rules.历史归档" :key="version.id" :data-testid="`archived-rule-${version.id}`">
                <td>{{ version.rule_version }}</td>
                <td>{{ conditionText(version.筛选条件) }}</td>
                <td>{{ fieldVersionText(version.字段版本) }}</td>
                <td>{{ version.历史归档时间 }}</td>
              </tr>
              <tr v-if="!rules.历史归档.length">
                <td colspan="4" class="empty-state">还没有历史归档版本</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 开跑 -->
    <div class="panel">
      <div class="panel-head">
        <h3>发起聚类分群</h3>
        <span class="panel-hint">一轮只采用一版完整规则；开跑后轮次锁定该版本</span>
      </div>
      <form class="inline-form" @submit.prevent="startNewRound">
        <label>
          <span>规则版本</span>
          <select v-model="startForm.rule_version" data-testid="start-rule-version">
            <option value="" disabled>请选择已生效规则</option>
            <option v-for="version in rules.已生效" :key="version.id" :value="version.rule_version">
              {{ version.rule_version }} · {{ conditionText(version.筛选条件) }}
            </option>
          </select>
        </label>
        <label class="sample-picker">
          <span>实际样本</span>
          <label v-for="sample in samples" :key="sample.sample_id" class="check-item">
            <input type="checkbox" :value="sample.sample_id" v-model="startForm.sample_ids" />
            {{ sample.sample_id }}（{{ sample.年龄 }} 岁）
          </label>
        </label>
        <button class="btn primary" type="submit" data-testid="start-round">开跑</button>
      </form>
    </div>

    <!-- 聚类历史 -->
    <div class="panel">
      <div class="panel-head">
        <h3>聚类历史</h3>
        <span class="panel-hint">回执标同一规则版本；重放使用归档规则与归档样本</span>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>轮次</th><th>规则版本</th><th>样本名单</th><th>实际名单</th><th>标签状态</th>
            <th>轮次状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="round in rounds" :key="round.id" :data-testid="`round-${round.id}`" :data-status="round.状态">
            <td>{{ round.轮次编号 }}</td>
            <td :data-testid="`round-rule-${round.id}`">{{ round.rule_version }}</td>
            <td>{{ round.样本名单.join('、') || '—' }}</td>
            <td>{{ round.实际名单.join('、') || '—' }}</td>
            <td>{{ labelStatusText(round) }}</td>
            <td>{{ round.状态 }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`round-detail-${round.id}`" @click="openRound(round)">
                看明细
              </button>
              <button
                v-if="round.状态 === '待处理'"
                class="link" type="button" :data-testid="`apply-${round.id}`"
                @click="applyLatest(round)"
              >
                应用新版
              </button>
              <button
                v-if="round.状态 === '待处理'"
                class="link" type="button" :data-testid="`submit-${round.id}`"
                @click="submitRound(round)"
              >
                提交生效
              </button>
              <button
                v-if="round.状态 === '已生效'"
                class="link" type="button" :data-testid="`archive-${round.id}`"
                @click="archiveRound(round)"
              >
                归档
              </button>
              <button class="link" type="button" :data-testid="`replay-${round.id}`" @click="replay(round)">
                重放
              </button>
            </td>
          </tr>
          <tr v-if="!rounds.length">
            <td colspan="7" class="empty-state">还没有聚类轮次</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 明细 -->
    <div v-if="detail" class="panel" data-testid="round-detail">
      <div class="panel-head">
        <h3>{{ detail.round.轮次编号 }} · 明细</h3>
        <button class="btn ghost" type="button" @click="detail = null">收起</button>
      </div>
      <div class="panel-grid">
        <div class="panel-col">
          <h4>标签与分类结果</h4>
          <table class="data-table">
            <thead>
              <tr><th>样本</th><th>年龄</th><th>分类结果</th><th>规则版本</th><th>标签状态</th></tr>
            </thead>
            <tbody>
              <tr v-for="label in detail.labels" :key="label.id">
                <td>{{ label.sample_id }}</td>
                <td>{{ label.样本快照?.年龄 ?? '—' }}</td>
                <td>{{ label.分类结果 }}</td>
                <td>{{ label.rule_version }}</td>
                <td>{{ label.状态 }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="panel-col">
          <h4>运行回执</h4>
          <dl class="receipt">
            <dt>采用规则版本</dt><dd data-testid="receipt-rule-version">{{ detail.receipt.rule_version }}</dd>
            <dt>轮次状态</dt><dd>{{ detail.receipt.状态 }}</dd>
            <dt>样本数量</dt><dd>{{ detail.receipt.样本数量 }}</dd>
            <dt>20 岁样本分类</dt>
            <dd data-testid="receipt-age20">
              {{ detail.receipt['20岁样本分类结果']?.分类结果 ?? '本批次无 20 岁样本' }}
            </dd>
          </dl>
        </div>
      </div>
    </div>

    <!-- 重放结果 -->
    <div v-if="replayResult" class="panel" data-testid="replay-result">
      <div class="panel-head">
        <h3>重放结果 · {{ replayResult.round_no }}</h3>
        <button class="btn ghost" type="button" @click="replayResult = null">收起</button>
      </div>
      <p class="panel-hint">
        采用归档规则 {{ replayResult.rule_version }}，原结果保持不变：{{ replayResult.original_unchanged ? '是' : '否' }}
      </p>
      <div class="panel-grid">
        <div class="panel-col">
          <h4>计算名单</h4>
          <p data-testid="replay-computed">{{ replayResult.computed_ids.join('、') || '—' }}</p>
          <h4>归档名单</h4>
          <p data-testid="replay-archived">{{ replayResult.archived_ids.join('、') || '—' }}</p>
        </div>
        <div class="panel-col">
          <h4>差异</h4>
          <dl class="receipt">
            <dt>漏标样本</dt><dd data-testid="replay-missing">{{ replayResult.diff.漏标样本.join('、') || '无' }}</dd>
            <dt>多出样本</dt><dd>{{ replayResult.diff.多出样本.join('、') || '无' }}</dd>
            <dt>分类变化</dt>
            <dd>
              {{ replayResult.diff.分类变化.map((item) => `${item.sample_id}: ${item.归档分类} → ${item.重算分类}`).join('；') || '无' }}
            </dd>
            <dt>是否一致</dt><dd>{{ replayResult.diff.是否一致 ? '一致' : '不一致' }}</dd>
          </dl>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  loadReceipt,
  loadRound,
  loadRounds,
  loadRuleGroups,
  loadSamples,
  publishRule,
  replayRound,
  runRoundAction,
  saveDraft,
  startRound,
} from '@/api/scenario-label'
import type {
  LabelItem,
  Receipt,
  ReplayResult,
  RoundItem,
  RuleCondition,
  RuleDraft,
  RuleVersion,
  SampleItem,
} from '@/api/scenario-label'

const OPERATORS = ['>=', '>', '<=', '<', '==']
const rules = ref<{ 草稿: RuleDraft[]; 已生效: RuleVersion[]; 历史归档: RuleVersion[] }>({
  草稿: [],
  已生效: [],
  历史归档: [],
})
const samples = ref<SampleItem[]>([])
const rounds = ref<RoundItem[]>([])
const detail = ref<{ round: RoundItem; labels: LabelItem[]; receipt: Receipt } | null>(null)
const replayResult = ref<ReplayResult | null>(null)
const message = ref('')
const messageError = ref(false)
const editState = reactive<Record<number, { field: string; operator: string; value: string; fieldVersion: string }>>({})
const startForm = reactive<{ rule_version: string; sample_ids: string[] }>({
  rule_version: '',
  sample_ids: [],
})

const cards = computed(() => [
  { label: '规则草稿', value: rules.value.草稿.length },
  { label: '已生效版本', value: rules.value.已生效.length },
  { label: '历史归档版本', value: rules.value.历史归档.length },
  { label: '聚类轮次', value: rounds.value.length },
])

function conditionText(condition: RuleCondition | null): string {
  if (!condition) return '—'
  return `${condition.字段} ${condition.运算符} ${condition.值}`
}

function fieldVersionText(fieldVersions: Record<string, string>): string {
  const entries = Object.entries(fieldVersions || {})
  return entries.length ? entries.map(([field, version]) => `${field}=${version}`).join('，') : '—'
}

function labelStatusText(round: RoundItem): string {
  const statuses = new Set(round.标签.map((label) => label.状态))
  if (!statuses.size) return '—'
  return Array.from(statuses).join(' / ')
}

function setMessage(text: string, error = false) {
  message.value = text
  messageError.value = error
}

function syncEditState() {
  for (const draft of rules.value.草稿) {
    const existing = editState[draft.id]
    if (existing) continue
    editState[draft.id] = {
      field: draft.筛选条件?.字段 ?? '',
      operator: draft.筛选条件?.运算符 ?? '>=',
      value: String(draft.筛选条件?.值 ?? ''),
      fieldVersion: Object.values(draft.字段版本 || {})[0] ?? '',
    }
  }
}

function buildCondition(draft: RuleDraft): RuleCondition {
  const state = editState[draft.id]
  const raw = state.value.trim()
  const numeric = Number(raw)
  return {
    字段: state.field.trim(),
    运算符: state.operator,
    值: raw !== '' && !Number.isNaN(numeric) ? numeric : raw,
  }
}

function buildFieldVersions(draft: RuleDraft): Record<string, string> {
  const state = editState[draft.id]
  const field = state.field.trim()
  return field && state.fieldVersion.trim() ? { [field]: state.fieldVersion.trim() } : {}
}

async function reloadRules() {
  const payload = await loadRuleGroups()
  rules.value = payload.groups
  syncEditState()
}

async function reloadRounds() {
  const payload = await loadRounds()
  rounds.value = payload.items
}

async function reloadAll() {
  try {
    await Promise.all([reloadRules(), reloadRounds(), reloadSamples()])
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '数据读取失败', true)
  }
}

async function reloadSamples() {
  const payload = await loadSamples()
  samples.value = payload.items
}

async function saveRuleDraft(draft: RuleDraft) {
  try {
    const result = await saveDraft(draft.id, {
      condition: buildCondition(draft),
      field_versions: buildFieldVersions(draft),
      segment_label: draft.分群标签,
    })
    setMessage(result.message, !result.ok)
    await reloadAll()
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '保存草稿失败', true)
  }
}

async function publishDraft(draft: RuleDraft) {
  try {
    const result = await publishRule({
      label_id: draft.id,
      condition: buildCondition(draft),
      field_versions: buildFieldVersions(draft),
      segment_label: draft.分群标签,
    })
    setMessage(result.message, !result.ok)
    await reloadAll()
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '发布失败', true)
  }
}

async function startNewRound() {
  if (!startForm.rule_version) {
    setMessage('请选择一版已生效规则', true)
    return
  }
  if (!startForm.sample_ids.length) {
    setMessage('请选择本轮要处理的样本', true)
    return
  }
  try {
    const result = await startRound({
      rule_version: startForm.rule_version,
      sample_ids: [...startForm.sample_ids],
      cluster_config_id: 3,
    })
    setMessage(result.message, !result.ok)
    await reloadAll()
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '开跑失败', true)
  }
}

async function openRound(round: RoundItem) {
  try {
    const payload = await loadRound(round.id)
    detail.value = { round: payload.round, labels: payload.labels, receipt: payload.receipt }
    replayResult.value = null
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '明细读取失败', true)
  }
}

async function applyLatest(round: RoundItem) {
  await roundAction(round, '应用新版')
}

async function submitRound(round: RoundItem) {
  await roundAction(round, '提交生效')
}

async function archiveRound(round: RoundItem) {
  await roundAction(round, '归档')
}

async function roundAction(round: RoundItem, action: string) {
  try {
    const result = await runRoundAction(round.id, action)
    setMessage(result.message, !result.ok)
    await reloadAll()
    if (result.ok) {
      const receipt = await loadReceipt(round.id)
      detail.value = { round: result.round ?? round, labels: result.round?.标签 ?? [], receipt: receipt.receipt }
    }
  } catch (error) {
    setMessage(error instanceof Error ? error.message : `${action}失败`, true)
  }
}

async function replay(round: RoundItem) {
  try {
    const result = await replayRound(round.id)
    replayResult.value = result
    setMessage(result.message, !result.ok)
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '重放失败', true)
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; margin-bottom: 14px; }
.panel-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
.panel-head h3 { margin: 0; font-size: 15px; }
.panel-hint { color: var(--muted); font-size: 12px; }
.panel-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.panel-col h4 { margin: 10px 0 6px; font-size: 13px; }
.inline-form { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; }
.inline-form label > span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.inline-form select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; }
.sample-picker { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.sample-picker > span { flex-basis: 100%; }
.check-item { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; }
.mini { width: 74px; border: 1px solid var(--border); border-radius: 4px; padding: 2px 4px; margin-right: 2px; }
.notice { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e3a8a; border-radius: 6px; padding: 8px 12px; font-size: 13px; }
.notice-error { background: #fef2f2; border-color: #fecaca; color: #b42318; }
.receipt { display: grid; grid-template-columns: 130px 1fr; gap: 6px 10px; margin: 0; }
.receipt dt { color: var(--muted); font-size: 12px; }
.receipt dd { margin: 0; font-size: 13px; }
</style>
