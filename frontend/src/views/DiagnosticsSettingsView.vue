<script setup lang="ts">
import { ClipboardCopy, Download, Trash2 } from 'lucide-vue-next'
import { ref } from 'vue'
import { confirmDialog } from '../platform/dialogs'
import {
  clearDiagnosticEntries,
  copyText,
  downloadDiagnosticEntries,
  listDiagnosticEntries,
  serializeDiagnosticPayload,
  type DiagnosticEntry,
} from '../platform/diagnostics'

const diagnostics = ref<DiagnosticEntry[]>(listDiagnosticEntries())
const notice = ref('')
const error = ref('')
const moduleLabels: Record<string, string> = {
  startup:'启动', library:'题库', practice:'练习', import:'导入',
  settings:'模型与设置', ai:'AI 功能', vocabulary:'单词本', update:'程序更新',
}

async function copyDiagnostics() {
  if (!diagnostics.value.length) return
  await copyText(serializeDiagnosticPayload(diagnostics.value))
  notice.value = `已复制 ${diagnostics.value.length} 条脱敏诊断`
}

function exportDiagnostics() {
  const count = downloadDiagnosticEntries()
  notice.value = count ? `已导出 ${count} 条脱敏诊断` : '暂无可导出的诊断'
}

async function clearDiagnostics() {
  if (!diagnostics.value.length) return
  if (!await confirmDialog({ title: '清空本地诊断', message: '确认清空全部本地诊断吗？清空后不可恢复。', confirmLabel: '清空', danger: true })) return
  clearDiagnosticEntries()
  diagnostics.value = []
  notice.value = '本地诊断已清空'
}
</script>

<template>
  <div class="page updates-page">
    <div class="page-head">
      <div>
        <h1>诊断日志</h1>
      </div>
    </div>

    <div v-if="error" class="warning" role="alert">{{ error }}</div>
    <div v-if="notice" class="settings-success" role="status">{{ notice }}</div>

    <section class="card update-diagnostics">
      <header>
        <div><h2>本地脱敏诊断</h2></div>
        <strong>{{ diagnostics.length }} 条</strong>
      </header>
      <div class="update-actions">
        <button class="button secondary" type="button" :disabled="!diagnostics.length" @click="copyDiagnostics"><ClipboardCopy :size="16" />复制诊断</button>
        <button class="button" type="button" :disabled="!diagnostics.length" @click="exportDiagnostics"><Download :size="16" />导出 JSON</button>
        <button class="button ghost" type="button" :disabled="!diagnostics.length" @click="clearDiagnostics"><Trash2 :size="16" />清空</button>
      </div>
      <div v-if="diagnostics.length" class="diagnostic-list">
        <details v-for="item in diagnostics" :key="`${item.createdAt}:${item.event}`">
          <summary><span><strong>{{ moduleLabels[item.module] || item.module }}</strong><small>{{ new Date(item.createdAt).toLocaleString('zh-CN') }} · {{ item.event }}</small></span><code>{{ item.errorCategory }}</code></summary>
          <p>{{ item.symptom }}</p><small>应用版本 {{ item.appVersion }}</small>
        </details>
      </div>
      <p v-else class="diagnostic-empty">暂无本地脱敏诊断。</p>
    </section>
  </div>
</template>

<style scoped>
.updates-page{max-width:1280px}.update-diagnostics{display:grid;gap:18px;margin-bottom:16px}.update-diagnostics>header{display:flex;align-items:flex-start;gap:12px}.update-diagnostics>header{justify-content:space-between}.update-diagnostics h2{font-size:1.25rem;margin:0 0 7px}.update-diagnostics header p{margin:0;color:var(--muted);line-height:1.65}.update-actions{display:flex;flex-wrap:wrap;gap:9px}@media(max-width:720px){.update-diagnostics>header{display:block}.update-diagnostics>header>strong{display:block;margin-top:10px}.update-actions .button{flex:1}}
.diagnostic-list{display:grid;gap:8px;margin-top:16px}.diagnostic-list details{border:1px solid var(--line);border-radius:8px;background:var(--surface-solid)}.diagnostic-list summary{display:flex;justify-content:space-between;gap:12px;padding:13px;cursor:pointer}.diagnostic-list summary span{display:grid;gap:4px}.diagnostic-list summary small,.diagnostic-list>details>small{color:var(--muted)}.diagnostic-list details>p,.diagnostic-list details>small{display:block;margin:0;padding:0 13px 12px}
@media(max-width:720px){.diagnostic-list summary{align-items:flex-start;flex-direction:column}}
</style>
