<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { Check, ChevronDown } from 'lucide-vue-next'

defineProps<{ value: string }>()
const emit = defineEmits<{ change: [value: string] }>()
const open = ref(false), trigger = ref<HTMLButtonElement>(), panel = ref<HTMLElement>()
const options = [
  ['new', '新词（尚未学习）'], ['all', '全部单词'], ['review', '每日一背'],
  ['frequent', '高频词'], ['learning', '学习中'], ['mastered', '已掌握'], ['pending', '等待翻译'],
] as const
async function toggle() {
  open.value = !open.value
  if (open.value) { await nextTick(); panel.value?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')?.focus() }
}
function close() { open.value = false; trigger.value?.focus({ preventScroll: true }) }
function choose(value: string) { close(); emit('change', value) }
function keys(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close() }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  event.preventDefault(); event.stopPropagation()
  const buttons = [...(panel.value?.querySelectorAll<HTMLButtonElement>('button') || [])]
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
  buttons[event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus()
}
</script>

<template>
  <div class="study-filter" @keydown="keys" @focusout="event => { if (event.relatedTarget && !(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node)) open = false }">
    <button ref="trigger" class="button ghost study-filter-trigger" type="button" :aria-expanded="open" @click="toggle">
      <span>筛选：<strong>{{ options.find(option => option[0] === value)?.[1] || value }}</strong></span><ChevronDown :size="16" />
    </button>
    <template v-if="open">
      <button class="filter-dismiss" tabindex="-1" type="button" aria-label="关闭筛选" @click="close"></button>
      <div ref="panel" class="study-filter-panel" role="group" aria-label="每日一学单词筛选">
        <button v-for="option in options" :key="option[0]" type="button" :aria-pressed="option[0] === value" @click="choose(option[0])">
          <span>{{ option[1] }}</span><Check v-if="option[0] === value" :size="17" />
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.study-filter{position:relative;flex:none;z-index:5;max-width:100%}
.study-filter-trigger{min-height:40px;color:var(--muted)!important;border:1px solid var(--line)!important;background:var(--surface-solid)!important;font-size:.875rem;max-width:100%;text-align:left}
.study-filter-trigger strong{color:var(--ink);font-weight:600}
.filter-dismiss{position:fixed;inset:0;z-index:1;border:0;background:transparent}
.study-filter-panel{position:absolute;top:calc(100% + 8px);left:0;z-index:2;width:245px;max-width:calc(100vw - 32px);max-height:min(320px,calc(100dvh - 160px));overflow-y:auto;padding:7px;border:1px solid var(--line);border-radius:13px;background:var(--surface-solid);box-shadow:var(--shadow)}
.study-filter-panel button{display:flex;width:100%;min-height:40px;align-items:center;justify-content:space-between;padding:8px 12px;border:0;border-radius:8px;background:transparent;color:var(--ink);font:inherit;font-size:.875rem;text-align:left;cursor:pointer}
.study-filter-panel button:hover,.study-filter-panel button[aria-pressed=true]{background:var(--primary-soft);color:var(--primary)}
.study-filter-panel button:focus-visible,.study-filter-trigger:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
</style>
