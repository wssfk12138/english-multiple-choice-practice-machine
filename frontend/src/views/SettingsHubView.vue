<script setup lang="ts">
import {
  BookOpen,
  CloudDownload,
  Database,
  Download,
  Eye,
  ExternalLink,
  FileWarning,
  Import,
  MonitorSmartphone,
  Moon,
  NotebookText,
  Settings2,
  Trash2,
} from 'lucide-vue-next'
import { useThemeState } from '../composables/useThemeState'
import { VOCAB_DISPLAY_OPTIONS, loadVocabDisplayConfig } from '../services/vocabularyDisplayConfig'
import '../settings-hub.css'

const dark = useThemeState()
function vocabularySummary() {
  const config = loadVocabDisplayConfig()
  return `已开启 ${VOCAB_DISPLAY_OPTIONS.filter(option => config[option.key]).length} 项`
}

function toggleTheme() {
  dark.value = !document.documentElement.classList.contains('dark')
  localStorage.setItem('linjian-theme', dark.value ? 'dark' : 'light')
  document.documentElement.classList.toggle('dark', dark.value)
}

// 每个设置入口对应独立页面；维护三子页与安卓横屏"设备同步/更新/诊断日志"同构。
// 公测特有的远程题库能力按 NAV-04 保留，并归入设置中枢。
const groups = [
  {
    title: '题库与数据',
    items: [
      { label: '导入题库', to: '/imports', icon: Import },
      { label: '远程题库', to: '/remote-question-banks', icon: CloudDownload },
      { label: '回收站', to: '/trash', icon: Trash2 },
    ],
  },
  {
    title: 'AI 与模型',
    items: [
      { label: '模型与 API', to: '/settings/model', icon: Settings2 },
    ],
  },
  {
    title: '设备与维护',
    items: [
      { label: '设备同步', to: '/settings/sync', icon: MonitorSmartphone },
      { label: '更新中心', to: '/settings/updates', icon: Download },
      { label: '诊断日志', to: '/settings/diagnostics', icon: FileWarning },
    ],
  },
] as const
</script>

<template>
  <div class="page settings-hub">
    <div class="page-head compact-page-head">
      <div class="page-title-row"><span class="page-title-icon-lucide"><Settings2 :size="24" /></span><h1>设置</h1></div>
    </div>

    <section class="settings-hub-group" aria-labelledby="hub-appearance-title">
      <h2 id="hub-appearance-title" class="settings-hub-group-title">外观与显示</h2>
      <div class="settings-hub-rows">
        <div class="settings-hub-row"><span class="settings-hub-row-icon"><Moon :size="19" /></span><strong>深色模式</strong><button class="settings-theme-switch" type="button" role="switch" :aria-checked="dark" aria-label="深色模式" @click="toggleTheme"><span /></button></div>
        <RouterLink class="settings-hub-row link" to="/settings/vocabulary">
          <span class="settings-hub-row-icon"><Eye :size="19" /></span>
          <span class="settings-hub-row-copy"><strong>单词本设置</strong><small>{{ vocabularySummary() }}</small></span>
          <b class="settings-hub-chevron" aria-hidden="true">›</b>
        </RouterLink>
      </div>
    </section>

    <section v-for="group in groups" :key="group.title" class="settings-hub-group" :aria-labelledby="`hub-${group.title}`">
      <h2 :id="`hub-${group.title}`" class="settings-hub-group-title">{{ group.title }}</h2>
      <div class="settings-hub-rows">
        <RouterLink v-for="item in group.items" :key="item.label" class="settings-hub-row link" :to="item.to">
          <span class="settings-hub-row-icon"><component :is="item.icon" :size="19" /></span>
          <span><strong>{{ item.label }}</strong></span>
          <b class="settings-hub-chevron" aria-hidden="true">›</b>
        </RouterLink>
      </div>
    </section>

    <section class="settings-hub-group" aria-labelledby="hub-help">
      <h2 id="hub-help" class="settings-hub-group-title">帮助</h2>
      <div class="settings-hub-rows">
        <RouterLink class="settings-hub-row link" to="/help">
          <span class="settings-hub-row-icon"><BookOpen :size="19" /></span>
          <span><strong>使用帮助</strong></span>
          <b class="settings-hub-chevron" aria-hidden="true">›</b>
        </RouterLink>
        <a class="settings-hub-row link" href="https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?h_camp=link&amp;h_src=YXBwX3NoYXJl&amp;link_id=0ad4723fda0b" target="_blank" rel="noopener noreferrer">
          <span class="settings-hub-row-icon"><NotebookText :size="19" /></span>
          <span><strong>问题反馈</strong></span>
          <ExternalLink class="settings-hub-chevron" :size="17" />
        </a>
      </div>
    </section>

    <div class="settings-hub-brand" aria-hidden="true"><Database :size="15" />学习数据保存在本机，可随时在设备同步中备份</div>
  </div>
</template>
