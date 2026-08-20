<script setup lang="ts">
import { RotateCcw, Trash2 } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import { del, get, post } from '../api'
import { useConfirm } from '../composables/useConfirm'

const items = ref<any[]>([])
const confirm = useConfirm()
const error = ref('')

async function load() {
  try { items.value = await get('/trash') } catch (cause) { error.value = String(cause) }
}

function typeLabel(type: string) {
  return ({ profile: '题库配置', paper: '试卷', import_job: '导入草稿' } as Record<string, string>)[type] || type
}

function purgeText(value: string) {
  const date = new Date(String(value).replace(' ', 'T') + 'Z')
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

async function restore(item: any) {
  try {
    await post(`/trash/${item.id}/restore`, {})
    await load()
  } catch (cause) { error.value = String(cause) }
}

async function purge(item: any) {
  const ok = await confirm({
    title: '永久删除？',
    message: `永久删除“${item.resource_name}”？此操作无法恢复。`,
    confirmText: '永久删除',
    danger: true,
  })
  if (!ok) return
  try {
    await del(`/trash/${item.id}`)
    await load()
  } catch (cause) { error.value = String(cause) }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div><span class="eyebrow">RECYCLE BIN</span><h1>回收站</h1><p class="lead">重要内容保留七天，可以恢复或提前永久删除。</p></div>
    </div>
    <div v-if="error" class="warning">{{ error }}</div>
    <div v-if="items.length" class="trash-list">
      <article v-for="item in items" :key="item.id" class="card trash-row">
        <div class="trash-meta">
          <span class="pill">{{ typeLabel(item.resource_type) }}</span>
          <strong>{{ item.resource_name }}</strong>
          <small>原配置：{{ item.profile_name || '已删除配置' }} · 将在 {{ purgeText(item.purge_after) }} 后清理</small>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="button secondary compact" type="button" @click="restore(item)"><RotateCcw :size="15" />恢复</button>
          <button class="button ghost danger compact" type="button" @click="purge(item)"><Trash2 :size="15" />永久删除</button>
        </div>
      </article>
    </div>
    <div v-else class="card empty"><strong>回收站是空的</strong><p>删除的题库配置、试卷和导入草稿会在这里保留七天。</p></div>
  </div>
</template>
