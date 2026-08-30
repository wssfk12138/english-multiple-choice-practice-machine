<script setup lang="ts">
import { BookMarked, BookOpenText, Brain, CircleHelp, CloudDownload, FileUp, Home, Library, MessageCircle, Moon, RefreshCw, Settings, Sun } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const dark = ref(false)
function applyTheme() {
  document.documentElement.classList.toggle('dark', dark.value)
  localStorage.setItem('linjian-theme', dark.value ? 'dark' : 'light')
}

function toggleTheme() {
  dark.value = !dark.value
  applyTheme()
}

onMounted(() => {
  dark.value = localStorage.getItem('linjian-theme') === 'dark'
    || (!localStorage.getItem('linjian-theme') && matchMedia('(prefers-color-scheme: dark)').matches)
  applyTheme()
})
</script>

<template>
  <div
    class="app-shell"
    :class="{ 'practice-shell': route.path.startsWith('/practice') }"
  >
    <aside class="sidebar" v-if="!route.path.startsWith('/practice')">
      <RouterLink class="brand" to="/">
        <span class="brand-mark"><img src="/assets/icons/brand-mark.png" alt="" /></span>
        <span class="brand-copy"><strong>英语刷题机</strong><small>考研英语一 · 本地题库</small></span>
      </RouterLink>
      <nav aria-label="主要导航">
        <RouterLink to="/"><Home :size="19" aria-hidden="true" /><span>首页</span></RouterLink>
        <RouterLink to="/library"><Library :size="19" aria-hidden="true" /><span>题库与练习</span></RouterLink>
        <RouterLink to="/wrong"><Brain :size="19" aria-hidden="true" /><span>错题本</span></RouterLink>
        <RouterLink to="/vocabulary"><BookMarked :size="19" aria-hidden="true" /><span>单词本</span></RouterLink>
        <RouterLink to="/imports"><FileUp :size="19" aria-hidden="true" /><span>导入题库</span></RouterLink>
        <RouterLink to="/remote-question-banks"><CloudDownload :size="19" aria-hidden="true" /><span>远程题库</span></RouterLink>
        <RouterLink to="/assistant">
          <MessageCircle :size="19" aria-hidden="true" /><span>AI 学习助手</span>
        </RouterLink>
        <RouterLink to="/settings"><Settings :size="19" aria-hidden="true" /><span>模型与设置</span></RouterLink>
        <RouterLink to="/updates"><RefreshCw :size="19" aria-hidden="true" /><span>更新中心</span></RouterLink>
        <RouterLink to="/help"><CircleHelp :size="19" aria-hidden="true" /><span>帮助与反馈</span></RouterLink>
      </nav>
      <div class="sidebar-note">
        <BookOpenText :size="18" />
        <p>慢一点读，答案常藏在句子之间。</p>
      </div>
      <button class="theme-button" type="button" @click="toggleTheme" :aria-label="dark ? '切换到浅色模式' : '切换到夜间模式'">
        <Sun v-if="dark" :size="18" /><Moon v-else :size="18" />
        {{ dark ? '浅色模式' : '夜间模式' }}
      </button>
    </aside>
    <main class="main-content">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
@media (max-width: 820px) {
  .sidebar nav { grid-template-columns: repeat(10, minmax(0, 1fr)); }
}
</style>
