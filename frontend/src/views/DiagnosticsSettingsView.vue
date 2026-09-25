<script setup lang="ts">
import { ClipboardCopy, Download, Trash2 } from 'lucide-vue-next'
import { ref } from 'vue'
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

async function copyDiagnostics() {
  if (!diagnostics.value.length) return
  await copyText(serializeDiagnosticPayload(diagnostics.value))
  notice.value = `已复制 ${diagnostics.value.length} 条脱敏诊断`
}

function exportDiagnostics() {
  const count = downloadDiagnosticEntries()
  notice.value = count ? `已导出 ${count} 条脱敏诊断` : '暂无可导出的诊断'
}

function clearDiagnostics() {
  if (!diagnostics.value.length || !confirm('确认清空全部本地诊断吗？')) return
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
    </section>
  </div>
</template>

<style scoped>
.updates-page{max-width:1000px}.update-diagnostics{display:grid;gap:18px;margin-bottom:16px}.update-diagnostics>header{display:flex;align-items:flex-start;gap:12px}.update-diagnostics>header{justify-content:space-between}.update-diagnostics h2{margin:0 0 7px}.update-diagnostics header p{margin:0;color:var(--muted);line-height:1.65}.update-actions{display:flex;flex-wrap:wrap;gap:9px}@media(max-width:720px){.update-diagnostics>header{display:block}.update-diagnostics>header>strong{display:block;margin-top:10px}.update-actions .button{flex:1}}
</style>
