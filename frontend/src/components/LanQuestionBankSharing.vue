<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { BookOpen, RefreshCw, Save } from 'lucide-vue-next'
import { get, put } from '../api'
const enabled=ref(false), selected=ref<number[]>([]), choices=ref<{id:number;name:string;papers:number}[]>([])
const busy=ref(false), error=ref(''), notice=ref('')
const selectedCount=computed(()=>selected.value.filter(id=>choices.value.some(c=>c.id===id)).length)
async function load(){busy.value=true;error.value='';notice.value='';try{const state=await get<{enabled:boolean;profileIds:number[];choices:typeof choices.value}>('/lan-sync/question-banks/sharing');enabled.value=state.enabled;selected.value=state.profileIds;choices.value=state.choices}catch(cause){error.value=String(cause)}finally{busy.value=false}}
async function save(){busy.value=true;error.value='';notice.value='';try{await put('/lan-sync/question-banks/sharing',{enabled:enabled.value,profile_ids:selected.value});notice.value=enabled.value?'共享快照已发布':'共享已关闭'}catch(cause){error.value=String(cause)}finally{busy.value=false}}
onMounted(load)
</script>
<template>
  <section class="card lan-bank-sharing" aria-labelledby="lan-bank-sharing-title" :aria-busy="busy">
    <header class="sharing-heading"><span class="sharing-symbol"><BookOpen :size="23" /></span><h2 id="lan-bank-sharing-title">局域网题库共享</h2><div class="sharing-tools"><label class="sharing-toggle"><input v-model="enabled" type="checkbox" :disabled="busy">开放共享</label><button class="icon-button" type="button" :disabled="busy" title="刷新共享状态" aria-label="刷新共享状态" @click="load"><RefreshCw :size="20" /></button></div></header>
    <fieldset :disabled="busy"><legend class="sr-only">共享范围</legend><div class="bank-grid"><label v-for="item in choices" :key="item.id" :class="{selected:selected.includes(item.id)}"><input v-model="selected" type="checkbox" :value="item.id"><span><strong>{{ item.name }}</strong><small>{{ item.papers }} 套试卷</small></span></label></div><p v-if="!choices.length" class="sharing-state">{{busy?'正在读取题库…':'暂无正式题库'}}</p></fieldset>
    <footer><span class="sharing-state" role="status">{{notice || `${enabled?'待发布共享范围':'共享已关闭'} · 已选 ${selectedCount} 个题库`}}</span><button class="button" type="button" :disabled="busy || (enabled && !selectedCount)" @click="save"><Save :size="18" />{{busy?'处理中…':enabled?'发布共享快照':'保存关闭状态'}}</button></footer>
    <p v-if="error" class="warning" role="alert">{{error}}</p>
  </section>
</template>
<style scoped>
.lan-bank-sharing{padding:22px;min-width:0}.sharing-heading{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.sharing-heading h2{font-size:1.25rem;margin:0;flex:1;min-width:0;overflow-wrap:anywhere}.sharing-symbol{display:grid;place-items:center;width:44px;height:44px;flex:0 0 44px;border-radius:12px;color:var(--primary);background:var(--primary-soft)}.sharing-tools{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.sharing-toggle{display:flex;align-items:center;gap:8px;font-size:.9375rem}.icon-button{width:44px;height:44px;min-width:44px;padding:0}fieldset{border:0;margin:18px 0;padding:0;min-width:0}.bank-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.bank-grid label{display:flex;gap:12px;align-items:center;min-width:0;min-height:74px;padding:14px;border:1px solid var(--line);border-radius:13px}.bank-grid label.selected{background:var(--primary-faint);border-color:var(--line-strong)}input{width:19px;height:19px;flex:0 0 19px;accent-color:var(--primary)}.bank-grid strong{display:block;overflow-wrap:anywhere;font-size:1rem}.bank-grid small{display:block;color:var(--muted);font-size:.8125rem;margin-top:3px}footer{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.sharing-state{font-size:.875rem;color:var(--muted)}
@media(max-width:540px){.lan-bank-sharing{padding:18px}.bank-grid{grid-template-columns:minmax(0,1fr)}.sharing-tools{width:100%;justify-content:space-between}.sharing-heading h2{font-size:1.125rem}footer .button{width:100%}}
</style>
