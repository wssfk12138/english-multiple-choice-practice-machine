<script setup lang="ts">
import { computed } from 'vue'
import VocabularyEnrichment from './VocabularyEnrichment.vue'
import type { VocabDisplayConfig, VocabDisplayKey } from '../services/vocabularyDisplayConfig'
const props = defineProps<{ entry: any; config: VocabDisplayConfig; expanded?: boolean; review?: boolean }>()
const show = (key: VocabDisplayKey) => props.expanded || props.config[key]
const groups = computed(() => [
  { key: 'synonyms' as const, label: '同义词辨析', items: props.entry.synonyms || [] },
  { key: 'antonyms' as const, label: '反义词辨析', items: props.entry.antonyms || [] },
  { key: 'similar_forms' as const, label: '形近词辨析', items: [...(props.entry.local_similar || []).map((v: any) => ({ ...v, local: true })), ...(props.entry.similar_forms || [])] },
])
</script>
<template>
  <div class="vocab-memory-content" :class="{ 'review-memory': review }">
    <section v-if="show('common_meaning') || (review && (show('phonetic') || show('part_of_speech')))" class="detail-section meaning-block">
      <div class="meaning-heading"><label v-if="show('common_meaning')">常用释义</label><span v-if="review && show('part_of_speech')">{{ entry.part_of_speech }}</span><span v-if="review && show('phonetic')">{{ entry.phonetic }}</span></div>
      <strong v-if="show('common_meaning')">{{ entry.common_meaning || entry.contextual_meaning || '释义待补充' }}</strong>
    </section>
    <section v-if="show('contextual') && entry.contextual_meaning && entry.contextual_meaning !== entry.common_meaning" class="detail-section"><label>语境释义</label><p>{{ entry.contextual_meaning }}</p></section>
    <section v-if="show('source_sentence') && entry.latest_sentence" class="detail-section source-sentence">
      <label>真题原句</label><blockquote>{{ entry.latest_sentence }}</blockquote>
      <small v-if="entry.occurrences?.length">{{ entry.occurrences[0].year || '未知年份' }} · {{ entry.occurrences[0].unit_title || entry.occurrences[0].unit_type }}</small>
    </section>
    <section v-if="show('model_sentence') && entry.generated_example?.sentence" class="detail-section"><label>AI 生成例句</label><blockquote>{{ entry.generated_example.sentence }}</blockquote><p>{{ entry.generated_example.translation }}</p></section>
    <VocabularyEnrichment :entry="entry" :show-morphology="show('morphology')" :show-sentence="false" />
    <template v-for="group in groups" :key="group.key">
      <section v-if="show(group.key) && group.items.length" class="detail-section discrimination-section"><label>{{ group.label }}</label><ul class="discrimination-list"><li v-for="(item,index) in group.items" :key="index"><strong>{{ item.word }}</strong><span>{{ item.note }}<em v-if="item.local" class="source-tag">本地</em></span></li></ul></section>
    </template>
    <section v-if="show('memory_hint') && entry.memory_hint" class="detail-section"><label>记忆提示</label><p>{{ entry.memory_hint }}</p></section>
    <section v-if="show('note') && entry.note" class="detail-section"><label>我的笔记</label><p>{{ entry.note }}</p></section>
  </div>
</template>
<style scoped>
.vocab-memory-content { min-width:0; overflow-wrap:anywhere; text-align:left; }
.meaning-heading { display:flex; flex-wrap:wrap; align-items:center; gap:8px; color:var(--muted); }
.meaning-block strong { display:block; line-height:1.65; }
.review-memory .meaning-block { text-align:center; }
.review-memory .meaning-heading { justify-content:center; }
blockquote { margin:10px 0; padding:12px 16px; border-left:3px solid var(--primary); background:var(--primary-soft); border-radius:0 8px 8px 0; font:17px/1.8 Cambria,Georgia,serif; }
p { white-space:pre-wrap; }
</style>
