<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { loadVocabDisplayConfig, saveVocabDisplayConfig, VOCAB_DISPLAY_OPTIONS } from '../services/vocabularyDisplayConfig'
const config = ref(loadVocabDisplayConfig())
const error = ref('')
// 平铺列表：开关顺序即卡片栏目顺序（VOCAB_DISPLAY_OPTIONS 是唯一权威来源）。
function refresh() { config.value = loadVocabDisplayConfig() }
function save() {
  try { saveVocabDisplayConfig(config.value); error.value = '' }
  catch { error.value = '显示设置未保存，请检查存储空间后重试' }
}
onMounted(() => { window.addEventListener('vocab-display-change', refresh); window.addEventListener('storage', refresh) })
onBeforeUnmount(() => { window.removeEventListener('vocab-display-change', refresh); window.removeEventListener('storage', refresh) })
</script>
<template>
  <section class="vocabulary-display-settings">
    <p class="scope-note">这些开关只作用于单词本的学习卡片；每日一背固定显示常用释义和真题原句。</p>
    <label v-for="option in VOCAB_DISPLAY_OPTIONS" :key="option.key">
      <span>{{ option.label }}</span><input v-model="config[option.key]" type="checkbox" @change="save">
    </label>
    <p v-if="error" class="error" role="alert">{{ error }} <button class="button ghost" @click="save">重试</button></p>
  </section>
</template>
<style scoped>
.vocabulary-display-settings { display:grid; gap:2px; text-align:left; min-width:0; }
.scope-note { margin:0 0 10px; color:var(--muted); font-size:13px; line-height:1.6; }
label { display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:40px; padding:6px 0; border-bottom:1px solid var(--line); cursor:pointer; }
input { width:18px; height:18px; flex-shrink:0; accent-color:var(--primary); }
</style>

