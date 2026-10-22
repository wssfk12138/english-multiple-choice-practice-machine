<script setup lang="ts">
import { computed, ref } from 'vue'
import { get, post } from '../api'
import { confirmDialog } from '../platform/dialogs'
import { translationQueueNotice } from '../services/vocabularyQueueStatus'

const props = defineProps<{ progress: any }>()
const emit = defineEmits<{ changed: [] }>()
const busy = ref(false), error = ref('')
const state = computed(() => props.progress.scheduler || {})
const pending = computed(() => ['pending','queued','translating','failed','enrichment_queued','enrichment_inflight','enrichment_failed'].some(key => Number(props.progress[key]) > 0))
const visible = computed(() => state.value.active || state.value.reason === 'unknown' || pending.value)
const notice = computed(() => translationQueueNotice(props.progress))
async function action(action: string, task?: string) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    if (action === 'acknowledge') {
      const current: any = await get('/vocabulary/request-status')
      const batches = current.unknown || []
      if (!batches.length) { emit('changed'); return }
      const words = new Set(batches.flatMap((batch: any) => batch.ids || [])).size
      if (!await confirmDialog({ title: '核查未知请求', message: [`${batches.length} 批请求涉及 ${words} 个词，可能已经产生费用。请先核查供应商记录与本地结果。`, '确认仅把这些批次标为已核查，不会自动发送；之后点击重试仍可能重复产生费用。'], confirmLabel: '已核查这些批次' })) return
      for (const batch of batches) await post('/vocabulary/request-runs', { action, attempt_id: batch.id, confirmed: true })
    } else {
      const current: any = task === 'enrichment' ? await get('/vocabulary/request-status') : null
      const retryIds = current?.retryIds?.enrichment || []
      await post('/vocabulary/request-runs', { action: task === 'enrichment' ? (retryIds.length ? 'retry' : 'continue') : action,
        task, ...(retryIds.length ? { entry_ids: retryIds } : {}), command_id: crypto.randomUUID() })
    }
    emit('changed')
  } catch { error.value = '操作未完成，请刷新任务状态后重试' }
  finally { busy.value = false }
}
</script>

<template>
  <section v-if="visible" class="vocab-request-notice card" aria-label="词汇任务状态" aria-live="polite">
    <p v-if="notice">{{ notice }}</p>
    <div class="request-actions">
      <button v-if="state.active" class="button ghost compact" :disabled="busy" @click="action('pause')">暂停词汇任务</button>
      <template v-else>
        <button v-if="state.reason === 'unknown'" class="button secondary compact" :disabled="busy" @click="action('acknowledge')">核查未知请求</button>
        <button v-if="progress.pending || progress.queued || progress.failed" class="button secondary compact" :disabled="busy || state.waitingConfiguration || state.reason === 'unknown'" @click="action('retry-all','translation')">重试翻译</button>
        <button v-if="progress.enrichment_queued || progress.enrichment_failed" class="button secondary compact" :disabled="busy || state.waitingConfiguration || state.reason === 'unknown'" @click="action('continue','enrichment')">继续补缺</button>
      </template>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.vocab-request-notice{padding:12px 16px;margin-bottom:12px;min-width:0}
p{margin:0;line-height:1.65;overflow-wrap:anywhere}
.request-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.error{margin-top:8px}
</style>
