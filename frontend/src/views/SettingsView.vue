<script setup lang="ts">
import {
  Check,
  ChevronDown,
  ChevronUp,
  CirclePlus,
  Eye,
  EyeOff,
  BookOpen,
  ExternalLink,
  KeyRound,
  LoaderCircle,
  PlugZap,
  RefreshCw,
  Save,
  Server,
  Trash2,
} from 'lucide-vue-next'
import { onMounted, reactive, ref } from 'vue'
import { del, get, post, put } from '../api'

type AdapterId = 'openai-chat' | 'openai-responses' | 'anthropic' | 'google' | 'kiro' | 'command-code'
const adapters: Array<{ id: AdapterId; label: string; placeholder: string; description: string; reasoning: boolean }> = [
  { id: 'openai-chat', label: 'OpenAI Chat Completions', placeholder: 'https://api.openai.com/v1', description: 'OpenAI 兼容的 /chat/completions 协议', reasoning: true },
  { id: 'openai-responses', label: 'OpenAI Responses', placeholder: 'https://api.openai.com/v1', description: 'OpenAI /responses 协议', reasoning: true },
  { id: 'anthropic', label: 'Anthropic Messages', placeholder: 'https://api.anthropic.com', description: 'Anthropic /v1/messages 协议', reasoning: false },
  { id: 'google', label: 'Google Gemini', placeholder: 'https://generativelanguage.googleapis.com/v1beta', description: 'Gemini generateContent 协议', reasoning: false },
  { id: 'kiro', label: 'Kiro', placeholder: 'https://runtime.us-east-1.kiro.dev', description: 'Kiro ksk_ API Key 与 event-stream 协议', reasoning: true },
  { id: 'command-code', label: 'Command Code', placeholder: 'https://api.commandcode.ai', description: 'Command Code /alpha/generate NDJSON 协议', reasoning: true },
]

type AiModel = {
  model_id: string
  display_name: string
  owned_by: string
  provider: string
  is_visible: boolean
  is_available: boolean
}

type AiProfile = {
  id: number
  name: string
  adapter: AdapterId
  base_url: string
  api_key?: string
  has_api_key: boolean
  enabled: boolean
  is_default: boolean
  default_model: string
  temperature: number
  max_tokens: number
  reasoning_effort: '' | 'low' | 'medium' | 'high'
  system_prompt: string
  models: AiModel[]
}

const profiles = ref<AiProfile[]>([])
const expanded = ref<number[]>([])
const busy = reactive<Record<string, boolean>>({})
const notices = reactive<Record<number, string>>({})
const message = ref('')
const error = ref('')
const creating = ref(false)

function blankProfile(): AiProfile {
  return {
    id: 0,
    name: '新 API 配置',
    adapter: 'openai-chat',
    base_url: 'http://127.0.0.1:11434/v1',
    api_key: '',
    has_api_key: false,
    enabled: true,
    is_default: false,
    default_model: '',
    temperature: 0.2,
    max_tokens: 0,
    reasoning_effort: '',
    system_prompt: '',
    models: [],
  }
}

const newProfile = reactive<AiProfile>(blankProfile())

function signalChanged() {
  window.dispatchEvent(new CustomEvent('linjian-ai-config-changed'))
}

function payload(profile: AiProfile) {
  return {
    name: profile.name,
    adapter: profile.adapter,
    base_url: profile.base_url,
    api_key: profile.api_key || null,
    enabled: profile.enabled,
    is_default: profile.is_default,
    default_model: profile.default_model,
    temperature: profile.temperature,
    max_tokens: profile.max_tokens,
    reasoning_effort: profile.reasoning_effort,
    system_prompt: profile.system_prompt,
  }
}

function adapterDefinition(profile: AiProfile) {
  return adapters.find(item => item.id === profile.adapter) || adapters[0]
}

function endpointPreview(profile: AiProfile) {
  const base = (profile.base_url || adapterDefinition(profile).placeholder).replace(/\/+$/, '')
  if (profile.adapter === 'openai-chat') return base + '/chat/completions'
  if (profile.adapter === 'openai-responses') return base + '/responses'
  if (profile.adapter === 'anthropic') return base + (base.endsWith('/v1') ? '/messages' : '/v1/messages')
  if (profile.adapter === 'google') return base + '/models/{model}:generateContent'
  if (profile.adapter === 'command-code') return base + '/alpha/generate'
  return base + '/'
}

function busyKey(action: string, id: number) {
  return `${action}:${id}`
}

function toggleExpanded(id: number) {
  expanded.value = expanded.value.includes(id)
    ? expanded.value.filter(item => item !== id)
    : [...expanded.value, id]
}

async function load() {
  try {
    const result = await get<AiProfile[]>('/ai/profiles')
    profiles.value = result.map(profile => ({ ...profile, adapter: profile.adapter || 'openai-chat', reasoning_effort: profile.reasoning_effort || '', api_key: '' }))
    error.value = ''
  } catch (cause) {
    error.value = String(cause)
  }
}

async function createProfile() {
  const key = busyKey('create', 0)
  if (busy[key]) return
  busy[key] = true
  try {
    const created = await post<AiProfile>('/ai/profiles', payload(newProfile))
    Object.assign(newProfile, blankProfile())
    creating.value = false
    message.value = `已添加“${created.name}”`
    error.value = ''
    await load()
    expanded.value = expanded.value.filter(id => id !== created.id)
    signalChanged()
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy[key] = false
  }
}

async function saveProfile(profile: AiProfile) {
  const key = busyKey('save', profile.id)
  if (busy[key]) return
  busy[key] = true
  try {
    await put(`/ai/profiles/${profile.id}`, payload(profile))
    notices[profile.id] = '配置已保存'
    message.value = ''
    error.value = ''
    await load()
    expanded.value = expanded.value.filter(id => id !== profile.id)
    signalChanged()
  } catch (cause) {
    error.value = String(cause)
  } finally {
    busy[key] = false
  }
}

async function toggleProfile(profile: AiProfile) {
  profile.enabled = !profile.enabled
  await saveProfile(profile)
}

async function syncModels(profile: AiProfile) {
  const key = busyKey('sync', profile.id)
  if (busy[key]) return
  busy[key] = true
  notices[profile.id] = ''
  try {
    const result = await post<{ models: unknown[] }>(`/ai/profiles/${profile.id}/models/sync`)
    notices[profile.id] = `已同步 ${result.models.length} 个模型`
    error.value = ''
    await load()
    signalChanged()
  } catch (cause) {
    notices[profile.id] = `同步失败：${String(cause)}`
  } finally {
    busy[key] = false
  }
}

async function testProfile(profile: AiProfile) {
  const key = busyKey('test', profile.id)
  if (busy[key]) return
  busy[key] = true
  notices[profile.id] = ''
  try {
    const result = await post<{ message: string }>(`/ai/profiles/${profile.id}/test`, {
      model: profile.default_model || null,
    })
    notices[profile.id] = result.message || '连接成功'
    error.value = ''
  } catch (cause) {
    notices[profile.id] = String(cause)
  } finally {
    busy[key] = false
  }
}

async function setModelVisible(profile: AiProfile, model: AiModel) {
  model.is_visible = !model.is_visible
  try {
    await put(`/ai/profiles/${profile.id}/models`, {
      model_id: model.model_id,
      is_visible: model.is_visible,
    })
    signalChanged()
  } catch (cause) {
    model.is_visible = !model.is_visible
    error.value = String(cause)
  }
}

async function setAllVisible(profile: AiProfile, visible: boolean) {
  try {
    await put(`/ai/profiles/${profile.id}/models/visibility`, { is_visible: visible })
    profile.models.forEach(model => { model.is_visible = visible })
    signalChanged()
  } catch (cause) {
    error.value = String(cause)
  }
}

async function removeProfile(profile: AiProfile) {
  if (!window.confirm(`删除 API 配置“${profile.name}”？已保存的对话不会被删除。`)) return
  try {
    await del(`/ai/profiles/${profile.id}`)
    message.value = `已删除“${profile.name}”`
    error.value = ''
    await load()
    signalChanged()
  } catch (cause) {
    error.value = String(cause)
  }
}

onMounted(() => {
  load()
})
</script>

<template>
  <div class="page ai-settings-page">
    <div class="page-head">
      <div>
        <h1>模型与 API</h1>
      </div>
      <button class="button" type="button" @click="creating=!creating">
        <CirclePlus :size="17" />添加 API 配置
      </button>
    </div>

    <div v-if="error" class="warning" role="alert">{{ error }}</div>
    <div v-if="message" class="settings-success"><Check :size="17" />{{ message }}</div>

    <section v-if="creating" class="api-profile-card new-profile">
      <div class="api-profile-heading">
        <span class="api-profile-icon"><CirclePlus :size="20" /></span>
        <div><h2>添加新的 API</h2></div>
      </div>
      <div class="api-profile-body">
        <div class="grid grid-2">
          <div class="field"><label>配置名称</label><input v-model.trim="newProfile.name" placeholder="例如：本地 Ollama"></div>
          <div class="field"><label>接口协议</label><select v-model="newProfile.adapter"><option v-for="adapter in adapters" :key="adapter.id" :value="adapter.id">{{ adapter.label }}</option></select><small>{{ adapterDefinition(newProfile).description }}</small></div>
        </div>
        <div class="grid grid-2"><div class="field"><label>默认模型（可稍后同步选择）</label><input v-model.trim="newProfile.default_model" placeholder="例如：qwen3:8b"></div><div class="field"><label>API Base URL</label><input v-model.trim="newProfile.base_url" :placeholder="adapterDefinition(newProfile).placeholder"><small>请求端点：{{ endpointPreview(newProfile) }}</small></div></div>
        <div class="field"><label>API Key</label><input v-model="newProfile.api_key" type="password" placeholder="本地接口通常可留空"></div>
        <div class="field"><label>默认推理强度</label><select v-model="newProfile.reasoning_effort" :disabled="!adapterDefinition(newProfile).reasoning"><option value="">未设置（由接口决定）</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select><small v-if="!adapterDefinition(newProfile).reasoning">该协议不发送推理强度，已选值会保留。</small></div>
        <div class="api-create-actions">
          <button class="button secondary" type="button" @click="creating=false">取消</button>
          <button class="button" type="button" :disabled="busy[busyKey('create',0)]" @click="createProfile">
            <LoaderCircle v-if="busy[busyKey('create',0)]" :size="16" class="spinning" />
            <Save v-else :size="16" />保存 API
          </button>
        </div>
      </div>
    </section>

    <div class="api-profile-list">
      <article v-for="profile in profiles" :key="profile.id" class="api-profile-card">
        <header class="api-profile-summary">
          <button class="api-profile-expand" type="button" :aria-expanded="expanded.includes(profile.id)" @click="toggleExpanded(profile.id)">
            <span class="api-profile-icon"><Server :size="20" /></span>
            <span class="api-profile-copy">
              <span><strong>{{ profile.name }}</strong><small v-if="profile.is_default">默认</small></span>
              <small>{{ profile.base_url }}</small>
            </span>
            <span class="api-profile-stats">
              <small>{{ profile.models.filter(model => model.is_visible && model.is_available).length }} 个模型可见</small>
              <span :class="{ online: profile.enabled }">{{ profile.enabled ? '已启用' : '已停用' }}</span>
            </span>
            <ChevronUp v-if="expanded.includes(profile.id)" :size="19" />
            <ChevronDown v-else :size="19" />
          </button>
          <button
            class="api-enable"
            type="button"
            role="switch"
            :aria-checked="profile.enabled"
            :aria-label="profile.enabled ? `停用 ${profile.name}` : `启用 ${profile.name}`"
            :class="{ active: profile.enabled }"
            @click="toggleProfile(profile)"
          ><span /></button>
        </header>

        <div v-if="expanded.includes(profile.id)" class="api-profile-body">
          <div class="grid grid-2">
            <div class="field"><label>配置名称</label><input v-model.trim="profile.name"></div>
            <div class="field"><label>接口协议</label><select v-model="profile.adapter"><option v-for="adapter in adapters" :key="adapter.id" :value="adapter.id">{{ adapter.label }}</option></select><small>{{ adapterDefinition(profile).description }}</small></div>
          </div>
          <div class="grid grid-2"><div class="field"><label>API Base URL</label><input v-model.trim="profile.base_url" :placeholder="adapterDefinition(profile).placeholder"><small>请求端点：{{ endpointPreview(profile) }}</small></div><div class="field"><label>API Key（留空不会清除）</label><div class="api-key-input"><KeyRound :size="17" /><input v-model="profile.api_key" type="password" :placeholder="profile.has_api_key ? '密钥已加密保存在本机' : '本地接口通常可留空'"></div></div></div>
          <div class="field">
              <label>默认模型</label>
              <select v-model="profile.default_model">
                <option value="">请选择默认模型</option>
                <option v-for="model in profile.models.filter(item => item.is_available)" :key="model.model_id" :value="model.model_id">
                  {{ model.display_name || model.model_id }}
                </option>
              </select>
            </div>
          <div class="grid grid-2">
            <div class="field"><label>Temperature</label><input v-model.number="profile.temperature" type="number" min="0" max="2" step=".1"></div>
            <div class="field"><label>默认推理强度</label><select v-model="profile.reasoning_effort" :disabled="!adapterDefinition(profile).reasoning"><option value="">未设置（由接口决定）</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select><small v-if="!adapterDefinition(profile).reasoning">该协议不发送推理强度，已选值会保留。</small></div>
          </div>
          <p class="field-hint">Temperature 控制回答的随机性与创造性：值越低越稳定、越适合判分和事实类任务（错题分析、题库导入、单词翻译建议 0.2–0.5）；越高越发散，适合头脑风暴。</p>
          <p class="field-hint">所有模型场景均由供应商决定最大输出长度；若长任务没有返回正文，程序会提示重试或切换模型/API 配置。</p>
          <div class="field"><label>附加系统提示词</label><textarea v-model="profile.system_prompt" rows="3" placeholder="对该 API 下的模型统一生效"></textarea></div>
          <label class="default-profile-check">
            <input v-model="profile.is_default" type="checkbox" :disabled="profile.is_default">
            设为默认 API（错题分析、单词翻译和题库校正会优先使用它）
          </label>

          <div class="api-actions">
            <button class="button" type="button" :disabled="busy[busyKey('save',profile.id)]" @click="saveProfile(profile)">
              <LoaderCircle v-if="busy[busyKey('save',profile.id)]" :size="16" class="spinning" />
              <Save v-else :size="16" />保存配置
            </button>
            <button class="button secondary" type="button" :disabled="busy[busyKey('sync',profile.id)]" @click="syncModels(profile)">
              <RefreshCw :size="16" :class="{spinning:busy[busyKey('sync',profile.id)]}" />同步模型
            </button>
            <button class="button secondary" type="button" :disabled="busy[busyKey('test',profile.id)] || !profile.default_model" @click="testProfile(profile)">
              <PlugZap :size="16" />测试连接
            </button>
            <button class="button ghost api-delete" type="button" @click="removeProfile(profile)">
              <Trash2 :size="16" />删除
            </button>
          </div>
          <p v-if="notices[profile.id]" class="api-profile-notice" role="status">{{ notices[profile.id] }}</p>

          <section class="api-model-section">
            <div class="api-model-heading">
              <div><h3>模型选择器</h3><p>关闭“显示”后，该模型仍保留在配置中，但不会出现在助手的切换菜单里。</p></div>
              <div>
                <button type="button" @click="setAllVisible(profile,true)"><Eye :size="15" />全部显示</button>
                <button type="button" @click="setAllVisible(profile,false)"><EyeOff :size="15" />全部隐藏</button>
              </div>
            </div>
            <div v-if="profile.models.length" class="api-model-list">
              <div v-for="model in profile.models" :key="model.model_id" class="api-model-row" :class="{ unavailable: !model.is_available }">
                <div><strong>{{ model.display_name || model.model_id }}</strong><small>{{ model.owned_by || model.provider || '接口模型' }}<template v-if="!model.is_available"> · 本次同步未发现</template></small></div>
                <button
                  type="button"
                  role="switch"
                  :aria-checked="model.is_visible"
                  :disabled="!model.is_available"
                  :class="{ active:model.is_visible }"
                  @click="setModelVisible(profile,model)"
                >
                  <Eye v-if="model.is_visible" :size="15" /><EyeOff v-else :size="15" />
                  {{ model.is_visible ? '显示' : '隐藏' }}
                </button>
              </div>
            </div>
            <div v-else class="api-model-empty">还没有模型列表。保存配置后点击“同步模型”。</div>
          </section>
        </div>
      </article>
    </div>

    <section class="settings-about card" aria-labelledby="settings-about-title">
      <div class="settings-about-heading">
        <span class="api-profile-icon"><BookOpen :size="20" /></span>
        <div><h2 id="settings-about-title">帮助与关于</h2></div>
      </div>
      <div class="settings-about-actions">
        <RouterLink class="button secondary" to="/help"><BookOpen :size="16" />使用帮助</RouterLink>
        <a class="button ghost" href="https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?h_camp=link&amp;h_src=YXBwX3NoYXJl&amp;link_id=0ad4723fda0b" target="_blank" rel="noopener noreferrer"><ExternalLink :size="16" />问题反馈</a>
      </div>
    </section>
  </div>
</template>
