import { createRouter, createWebHistory } from 'vue-router'

// 路由懒加载：按需加载视图，减小首屏 JS 体积
const DashboardView = () => import('./views/DashboardView.vue')
const LibraryView = () => import('./views/LibraryView.vue')
const PracticeView = () => import('./views/PracticeView.vue')
const WrongView = () => import('./views/WrongView.vue')
const VocabularyView = () => import('./views/VocabularyView.vue')
const ImportView = () => import('./views/ImportView.vue')
const AiAssistant = () => import('./components/AiAssistant.vue')
const SettingsView = () => import('./views/SettingsView.vue')
const TrashView = () => import('./views/TrashView.vue')

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: DashboardView },
    { path: '/library', component: LibraryView },
    { path: '/practice/:id', component: PracticeView },
    { path: '/wrong', component: WrongView },
    { path: '/vocabulary', component: VocabularyView },
    { path: '/imports', component: ImportView },
    { path: '/assistant', component: AiAssistant },
    { path: '/settings', component: SettingsView },
    { path: '/trash', component: TrashView },
  ],
})
