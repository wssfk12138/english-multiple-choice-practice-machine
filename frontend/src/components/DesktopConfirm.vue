<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { confirmation, finishConfirmation } from '../services/desktopConfirm'
const dialog=ref<HTMLDialogElement|null>(null)
watch(()=>confirmation.open, async open=>{await nextTick(); if(open) dialog.value?.showModal(); else dialog.value?.close()})
</script>
<template><Teleport to="body"><dialog ref="dialog" class="desktop-confirm" aria-labelledby="desktop-confirm-title" @cancel.prevent="finishConfirmation(false)"><h2 id="desktop-confirm-title">确认操作</h2><p>{{ confirmation.message }}</p><div class="confirm-actions"><button autofocus class="button secondary" @click="finishConfirmation(false)">取消</button><button class="button" @click="finishConfirmation(true)">确认</button></div></dialog></Teleport></template>
<style scoped>
.desktop-confirm{width:min(460px,calc(100vw - 32px));max-height:calc(100dvh - 32px);overflow:auto;border:1px solid var(--line);border-radius:14px;background:var(--surface);color:var(--ink);padding:24px;box-shadow:0 18px 70px #0004}.desktop-confirm::backdrop{background:#0007}.desktop-confirm p{white-space:pre-wrap;overflow-wrap:anywhere}.confirm-actions{display:flex;justify-content:flex-end;gap:12px;margin-top:24px}
</style>
