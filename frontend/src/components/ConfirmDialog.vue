<script setup lang="ts">
import { inject } from 'vue'
import { AlertTriangle } from 'lucide-vue-next'
import { CONFIRM_KEY } from '../composables/useConfirm'
import type { ConfirmState } from '../composables/useConfirm'

const state = inject(CONFIRM_KEY)!

function close(result: boolean) {
  state.visible.value = false
  state.resolve.value?.(result)
  state.resolve.value = null
}
</script>

<template>
  <Teleport to="body">
    <Transition name="confirm-fade">
      <div
        v-if="state.visible.value"
        class="confirm-overlay"
        role="presentation"
        @click.self="close(false)"
      >
        <div class="confirm-dialog" role="alertdialog" aria-modal="true" :aria-label="state.options.value?.title">
          <div class="confirm-icon" :class="{ danger: state.options.value?.danger }">
            <AlertTriangle :size="22" aria-hidden="true" />
          </div>
          <div class="confirm-copy">
            <h3>{{ state.options.value?.title }}</h3>
            <p v-if="state.options.value?.message">{{ state.options.value.message }}</p>
          </div>
          <div class="confirm-actions">
            <button class="button secondary" type="button" @click="close(false)">
              {{ state.options.value?.cancelText || '取消' }}
            </button>
            <button
              class="button"
              :class="{ danger: state.options.value?.danger }"
              type="button"
              @click="close(true)"
            >
              {{ state.options.value?.confirmText || '确定' }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(20, 28, 24, 0.45);
  backdrop-filter: blur(4px);
}
.confirm-dialog {
  width: min(420px, 100%);
  display: flex;
  gap: 14px;
  align-items: flex-start;
  padding: 22px;
  border-radius: var(--radius-lg);
  background: var(--surface-solid);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
  color: var(--ink);
}
.confirm-icon {
  flex: 0 0 40px;
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: var(--primary-soft);
  color: var(--primary);
}
.confirm-icon.danger {
  background: var(--danger-soft);
  color: var(--danger);
}
.confirm-copy { flex: 1; min-width: 0; }
.confirm-copy h3 {
  margin: 2px 0 6px;
  font-size: 16px;
  font-weight: 700;
  line-height: 1.4;
}
.confirm-copy p {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--muted);
  white-space: pre-line;
}
.confirm-actions {
  display: flex;
  gap: 10px;
  margin-top: 4px;
}
.confirm-actions .button {
  min-width: 0;
  min-height: 38px;
  padding: 8px 14px;
  font-size: 13px;
  white-space: nowrap;
}
.confirm-actions .button.secondary { margin-top: 0; }

.confirm-fade-enter-active,
.confirm-fade-leave-active { transition: opacity 0.18s ease; }
.confirm-fade-enter-active .confirm-dialog,
.confirm-fade-leave-active .confirm-dialog { transition: transform 0.18s ease; }
.confirm-fade-enter-from,
.confirm-fade-leave-to { opacity: 0; }
.confirm-fade-enter-from .confirm-dialog,
.confirm-fade-leave-to .confirm-dialog { transform: translateY(8px) scale(0.98); }
</style>
