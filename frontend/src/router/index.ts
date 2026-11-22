import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import ScenarioLabelRules from '@/views/ScenarioLabelRules.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 场景标注的规则发布 / 聚类轮次 / 重放有专用工作台，优先于通用模块页匹配。
    { path: '/scenario_label/rules', name: 'scenario-label-rules', component: ScenarioLabelRules },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
