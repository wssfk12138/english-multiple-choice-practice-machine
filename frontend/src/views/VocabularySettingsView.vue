<script setup lang="ts">
import { computed, reactive } from 'vue'
import { ArrowLeft, Eye } from 'lucide-vue-next'
import VocabularyMaintenance from '../components/VocabularyMaintenance.vue'
import {
  VOCAB_DISPLAY_OPTIONS,
  loadVocabDisplayConfig,
  saveVocabDisplayConfig,
  type VocabDisplayKey,
} from '../services/vocabularyDisplayConfig'
import '../settings-hub.css'

type DisplayGroup = {
  title: string
  keys: VocabDisplayKey[]
  options: typeof VOCAB_DISPLAY_OPTIONS[number][]
}

const config = reactive<Record<VocabDisplayKey, boolean>>(loadVocabDisplayConfig())
const groups: DisplayGroup[] = ([
  { title: '词语信息', keys: ['common_meaning', 'contextual', 'phonetic', 'part_of_speech', 'source_sentence'] },
  { title: '释义与例句', keys: ['model_sentence', 'memory_hint'] },
  { title: '辨析与词形变化', keys: ['synonyms', 'antonyms', 'similar_forms', 'morphology'] },
  { title: '个人记录', keys: ['note'] },
] as { title: string; keys: VocabDisplayKey[] }[]).map(group => ({
  ...group,
  options: group.keys.map(key => VOCAB_DISPLAY_OPTIONS.find(option => option.key === key)!),
}))

const enabledCount = computed(() => VOCAB_DISPLAY_OPTIONS.filter(option => config[option.key]).length)

function toggle(key: VocabDisplayKey) {
  config[key] = !config[key]
  saveVocabDisplayConfig({ ...config })
}

function setGroup(group: DisplayGroup, value: boolean) {
  group.keys.forEach(key => { config[key] = value })
  saveVocabDisplayConfig({ ...config })
}
</script>

<template>
  <div class="page settings-hub vocabulary-settings">
    <div class="page-head compact-page-head">
      <div class="page-title-row">
        <RouterLink class="icon-button" to="/settings" aria-label="返回设置"><ArrowLeft :size="20" /></RouterLink>
        <span class="page-title-icon-lucide"><Eye :size="22" /></span>
        <h1>单词本设置</h1>
      </div>
    </div>

    <section v-for="group in groups" :key="group.title" class="settings-hub-group">
      <div class="display-group-head">
        <h2 class="settings-hub-group-title">{{ group.title }}</h2>
        <div class="display-group-actions">
          <button type="button" @click="setGroup(group, true)">全部开启</button>
          <button type="button" @click="setGroup(group, false)">全部关闭</button>
        </div>
      </div>
      <div class="display-options">
        <label v-for="option in group.options" :key="option.key" class="display-option">
          <span>{{ option.label }}</span>
          <input type="checkbox" :checked="config[option.key]" @change="toggle(option.key)">
        </label>
      </div>
    </section>

    <VocabularyMaintenance />

    <p class="settings-hub-brand">{{ enabledCount }} 个栏目已开启，设置保存在本机</p>
  </div>
</template>

<style scoped>
.display-group-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 0 8px; }
.display-group-head .settings-hub-group-title { margin: 0; }
.display-group-actions { display: flex; gap: 7px; }
.display-group-actions button { min-height: 36px; padding: 0 12px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface-solid); color: var(--muted); font-size: 13px; }
.display-group-actions button:hover { border-color: var(--primary); color: var(--primary); }
.display-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.display-option { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 60px; padding: 12px 16px; border: 1px solid var(--line); border-radius: 13px; background: var(--surface); cursor: pointer; }
.display-option span { font-size: 16px; font-weight: 600; }
.display-option input { flex: none; width: 19px; height: 19px; accent-color: var(--primary); cursor: pointer; }
@media (max-width: 760px) {
  .display-options { grid-template-columns: 1fr; }
  .display-group-head { align-items: flex-start; }
}
</style>
