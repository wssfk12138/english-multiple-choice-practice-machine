import { createRouter, createWebHistory } from 'vue-router'
import AiAssistant from './components/AiAssistant.vue'
import DashboardView from './views/DashboardView.vue'
import ImportView from './views/ImportView.vue'
import LibraryView from './views/LibraryView.vue'
import PracticeView from './views/PracticeView.vue'
import SettingsHubView from './views/SettingsHubView.vue'
import SettingsView from './views/SettingsView.vue'
import VocabularySettingsView from './views/VocabularySettingsView.vue'
import WrongAnalysisView from './views/WrongAnalysisView.vue'
import WrongView from './views/WrongView.vue'
import VocabularyView from './views/VocabularyView.vue'
import DailyStudyView from './views/DailyStudyView.vue'
import TrashView from './views/TrashView.vue'
import HelpView from './views/HelpView.vue'
import RemoteQuestionBanksView from './views/RemoteQuestionBanksView.vue'
import SyncSettingsView from './views/SyncSettingsView.vue'
import UpdatesSettingsView from './views/UpdatesSettingsView.vue'
import DiagnosticsSettingsView from './views/DiagnosticsSettingsView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: DashboardView },
    { path: '/library', component: LibraryView },
    { path: '/practice/:id', component: PracticeView },
    { path: '/wrong', component: WrongView },
    { path: '/wrong/:unitId/analysis', component: WrongAnalysisView },
    { path: '/wrong/analysis/:unitId', component: WrongAnalysisView },
    { path: '/vocabulary', component: VocabularyView },
    { path: '/vocabulary/daily-study', component: DailyStudyView },
    { path: '/imports', component: ImportView },
    { path: '/assistant', component: AiAssistant },
    { path: '/settings', component: SettingsHubView },
    { path: '/settings/model', component: SettingsView },
    { path: '/settings/vocabulary', component: VocabularySettingsView },
    { path: '/settings/sync', component: SyncSettingsView },
    { path: '/settings/updates', component: UpdatesSettingsView },
    { path: '/settings/diagnostics', component: DiagnosticsSettingsView },
    { path: '/trash', component: TrashView },
    { path: '/help', component: HelpView },
    { path: '/remote-question-banks', component: RemoteQuestionBanksView },
    { path: '/updates', redirect: '/settings/updates' },
  ],
})
