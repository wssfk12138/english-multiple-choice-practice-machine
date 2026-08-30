<script setup lang="ts">
import { ClipboardCopy, Download, ExternalLink, FileWarning, Trash2 } from 'lucide-vue-next'
import { ref } from 'vue'
import {
  clearDiagnosticEntries,
  copyText,
  downloadDiagnosticEntries,
  issueReportTemplate,
  listDiagnosticEntries,
  serializeDiagnosticPayload,
  type DiagnosticEntry,
} from '../platform/diagnostics'

const feedbackUrl = 'https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?h_camp=link&h_src=YXBwX3NoYXJl&link_id=0ad4723fda0b'
const entries = ref<DiagnosticEntry[]>(listDiagnosticEntries())
const notice = ref('')
const error = ref('')

const moduleLabels: Record<string, string> = {
  startup: '启动', library: '题库', practice: '练习', import: '导入',
  settings: '模型与设置', ai: 'AI 功能', vocabulary: '单词本',
  update: '程序更新',
}

async function copyTemplate() {
  try {
    await copyText(issueReportTemplate())
    notice.value = '问题报告模板已复制，请补充复现步骤后主动提交'
    error.value = ''
  } catch (cause) { error.value = `复制模板失败：${String(cause)}` }
}

async function copyDiagnostics() {
  try {
    if (!entries.value.length) return
    await copyText(serializeDiagnosticPayload(entries.value))
    notice.value = `已复制 ${entries.value.length} 条脱敏诊断`
    error.value = ''
  } catch (cause) { error.value = `复制诊断失败：${String(cause)}` }
}

function downloadDiagnostics() {
  const count = downloadDiagnosticEntries()
  notice.value = count ? `已导出 ${count} 条脱敏诊断` : '暂无可导出的诊断'
}

function clearDiagnostics() {
  if (!entries.value.length || !confirm('确认清空全部本地诊断吗？')) return
  clearDiagnosticEntries()
  entries.value = []
  notice.value = '本地诊断已清空'
}
</script>

<template>
  <div class="page help-page">
    <div class="page-head">
      <div><span class="eyebrow">PUBLIC BETA SUPPORT</span><h1>帮助与问题反馈</h1><p class="lead">诊断只保存在本机，只有你主动复制或导出时才会离开本机。</p></div>
      <a class="button" :href="feedbackUrl" target="_blank" rel="noopener noreferrer"><ExternalLink :size="16" />打开问题反馈</a>
    </div>
    <div v-if="error" class="warning" role="alert">{{ error }}</div>
    <div v-if="notice" class="settings-success" role="status">{{ notice }}</div>

    <section class="help-grid">
      <article class="card"><h2>快速排查</h2><ul><li>题库无法打开时，先确认文件格式和内容来源可信。</li><li>模型功能失败时，检查接口配置、模型可用性和账户状态。</li><li>页面异常时，记录所在功能、操作步骤、预期结果和实际结果。</li></ul></article>
      <article class="card"><h2>问题报告</h2><p>模板只自动填写应用版本，其余内容由你主动补充。请勿粘贴题库、答案或聊天正文。</p><button class="button secondary" type="button" @click="copyTemplate"><ClipboardCopy :size="16" />复制问题报告模板</button></article>
    </section>

    <section class="card diagnostics-card">
      <header><div><span class="diagnostic-icon"><FileWarning :size="20" /></span><div><h2>脱敏诊断</h2><p>最多 50 条、导出包不超过约 1 MiB，只含事件、模块、应用版本、时间、错误类别和短症状。</p></div></div><strong>{{ entries.length }} 条</strong></header>
      <p class="privacy-note">不包含 API Key、令牌、URL 参数、完整路径、IP、设备标识、题库或答案正文、聊天内容和学习记录；不会后台遥测或自动上传。</p>
      <div class="diagnostic-actions"><button class="button secondary" type="button" :disabled="!entries.length" @click="copyDiagnostics"><ClipboardCopy :size="16" />复制诊断</button><button class="button" type="button" :disabled="!entries.length" @click="downloadDiagnostics"><Download :size="16" />导出 JSON</button><button class="button ghost" type="button" :disabled="!entries.length" @click="clearDiagnostics"><Trash2 :size="16" />清空</button></div>
      <div v-if="entries.length" class="diagnostic-list"><details v-for="item in entries" :key="`${item.createdAt}:${item.event}`"><summary><span><strong>{{ moduleLabels[item.module] || item.module }}</strong><small>{{ new Date(item.createdAt).toLocaleString('zh-CN') }} · {{ item.event }}</small></span><code>{{ item.errorCategory }}</code></summary><p>{{ item.symptom }}</p><small>应用版本 {{ item.appVersion }}</small></details></div>
      <p v-else class="empty-state">目前没有本地诊断记录。</p>
    </section>
  </div>
</template>

<style scoped>
.help-page{max-width:1100px}.help-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-bottom:16px}.help-grid h2,.diagnostics-card h2{margin:0 0 10px}.help-grid p,.help-grid li,.diagnostics-card p{color:var(--muted);line-height:1.7}.help-grid li+li{margin-top:7px}.diagnostics-card>header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start}.diagnostics-card>header>div{display:flex;gap:12px}.diagnostic-icon{width:42px;height:42px;display:grid;place-items:center;border-radius:10px;color:var(--primary);background:var(--primary-soft)}.diagnostics-card>header p{margin:0}.privacy-note{padding:12px 14px;border:1px solid var(--line);border-radius:8px;background:var(--primary-faint)}.diagnostic-actions{display:flex;flex-wrap:wrap;gap:9px}.diagnostic-list{display:grid;gap:8px;margin-top:16px}.diagnostic-list details{border:1px solid var(--line);border-radius:8px;background:var(--surface-solid)}.diagnostic-list summary{display:flex;justify-content:space-between;gap:12px;padding:13px;cursor:pointer}.diagnostic-list summary span{display:grid;gap:4px}.diagnostic-list summary small,.diagnostic-list>details>small{color:var(--muted)}.diagnostic-list details>p,.diagnostic-list details>small{display:block;margin:0;padding:0 13px 12px}.empty-state{margin:16px 0 0}@media(max-width:720px){.help-grid{grid-template-columns:1fr}.diagnostics-card>header{align-items:center}.diagnostic-list summary{align-items:flex-start;flex-direction:column}}
</style>
