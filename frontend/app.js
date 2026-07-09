/**
 * 科普助手 · 科科 - Vue3 应用逻辑
 * 适配后端 API: http://localhost:8000
 */
const { createApp, reactive, ref, computed, onMounted, nextTick, watch } = Vue;

const API_BASE_URL = 'http://localhost:8000';

// 配置 marked
if (typeof marked !== 'undefined') {
    try {
        marked.setOptions({
            breaks: true,
            gfm: true,
        });
    } catch (e) {
        console.warn('marked 配置失败:', e);
    }
}

const app = createApp({
    setup() {
        const messagesContainer = ref(null);
        const inputTextarea = ref(null);
        const imageInput = ref(null);
        const renameInput = ref(null);

        // ===== 响应式状态 =====
        const state = reactive({
            // 会话管理
            sessions: [],
            currentSessionId: null,
            currentSessionTitle: '',
            loadingSessions: false,
            searchKeyword: '',
            searchTimer: null,
            editingSessionId: null,
            editingTitle: '',

            // 消息
            messages: [],
            loadingMessages: false,

            // 输入
            inputMessage: '',
            isSending: false,
            pendingImage: null,
            pendingImagePreview: null,
            abortController: null,  // 用于停止生成

            // 模型
            models: [],
            modelInfo: {},  // model_name -> { supports_image, description, ... }
            currentModel: 'deepseek-chat',
            defaultModel: 'deepseek-chat',
            deepThink: false,  // 深度思考模式

            // 预设
            presets: [],
            currentPresetId: '',
            currentPreset: null,

            // 认证
            authToken: localStorage.getItem('authToken') || null,
            currentUser: null,
            showLoginModal: false,
            loginUsername: '',
            loginNickname: '',
            loginMode: 'login', // 'login' 或 'register'
            loginLoading: false,
            showUserMenu: false,

            // UI
            sidebarOpen: window.innerWidth > 768,
            showExportMenu: false,
            showScrollBottom: false,
            autoScroll: true,
            darkMode: localStorage.getItem('darkMode') === 'true',

            // 快捷提问模板
            quickPrompts: [
                { icon: '🌈', text: '为什么天空是蓝色的？' },
                { icon: '🕳️', text: '黑洞到底是什么？' },
                { icon: '🧬', text: 'DNA是如何决定我们特征的？' },
                { icon: '🌱', text: '光合作用是怎么进行的？' },
                { icon: '⚡', text: '量子纠缠是什么意思？' },
                { icon: '💤', text: '为什么我们会做梦？' },
            ],

            // 统计
            stats: {
                total_sessions: 0,
                total_messages: 0,
                model_usage: {},
            },

            // Token 统计
            lastTokenUsage: null,  // {prompt_tokens, completion_tokens}
            totalTokenUsage: { prompt: 0, completion: 0 },

            // 预设管理
            showPresetModal: false,
            showPresetFormModal: false,  // 表单弹窗（独立于列表弹窗）
            presetModalMode: 'create', // 'create' or 'edit'
            editingPreset: null,
            presetForm: { name: '', description: '', system_prompt: '', icon: '🤖' },

            // 多模型对比
            showCompareModal: false,
            compareModels: [],  // 选中的对比模型列表
            compareResults: null,
            compareLoading: false,
            compareMessage: '',  // 对比的问题

            // 语音
            isListening: false,
            isSpeaking: false,
            speechRecognition: null,
            speakingMessageIndex: null,  // 当前正在朗读的消息索引

            // 工具调用 Agent
            agentMode: false,  // 是否启用 Agent 模式

            // Toast
            toast: null,
        });

        // ===== 计算属性 =====
        const currentSessionTitle = computed(() => {
            if (state.currentSessionId) {
                const session = state.sessions.find(s => s.session_id === state.currentSessionId);
                return session ? session.title : '对话中';
            }
            return state.currentPreset ? state.currentPreset.name + ' · 新对话' : '新对话';
        });

        // ===== 工具函数 =====
        function authHeaders() {
            return state.authToken ? { 'Authorization': `Bearer ${state.authToken}` } : {};
        }

        function showToast(message, type = 'info') {
            state.toast = { message, type };
            setTimeout(() => {
                state.toast = null;
            }, 3000);
        }

        function formatModelName(modelName) {
            if (!modelName) return '未知模型';
            const displayNames = {
                'deepseek-chat': 'DeepSeek Chat',
                'deepseek-coder': 'DeepSeek Coder',
                'deepseek-reasoner': 'DeepSeek Reasoner',
                'gpt-4o-mini': 'GPT-4o Mini',
                'gpt-4o': 'GPT-4o',
                'gpt-3.5-turbo': 'GPT-3.5 Turbo',
                'qwen-plus': '通义千问 Plus',
                'qwen-vl-plus': '通义千问 VL Plus',
                'claude-3-sonnet': 'Claude 3 Sonnet',
                'claude-3.5-sonnet': 'Claude 3.5 Sonnet',
            };
            return displayNames[modelName] || modelName;
        }

        function formatTime(timeStr) {
            if (!timeStr) return '';
            const date = new Date(timeStr);
            const now = new Date();
            const diff = now - date;
            const minutes = Math.floor(diff / 60000);
            const hours = Math.floor(diff / 3600000);
            const days = Math.floor(diff / 86400000);

            if (minutes < 1) return '刚刚';
            if (minutes < 60) return `${minutes}分钟前`;
            if (hours < 24) return `${hours}小时前`;
            if (days < 7) return `${days}天前`;
            return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
        }

        function renderMarkdown(content) {
            if (!content) return '';

            // 保护数学公式，避免 marked 把 LaTeX 的 \\ 当作转义字符吃掉
            const mathStore = [];
            const PLACEHOLDER_PREFIX = 'KATEXMATHPLACEHOLDER';

            // 1) 块级公式 $$...$$
            content = content.replace(/\$\$([\s\S]+?)\$\$/g, (m, formula) => {
                const idx = mathStore.length;
                mathStore.push({ formula, display: true });
                return `\n\n${PLACEHOLDER_PREFIX}${idx}MATH\n\n`;
            });

            // 2) 行内公式 $...$（避免匹配到 $$ 和跨行）
            content = content.replace(/\$([^\$\n]+?)\$/g, (m, formula) => {
                const idx = mathStore.length;
                mathStore.push({ formula, display: false });
                return `${PLACEHOLDER_PREFIX}${idx}MATH`;
            });

            // 用 marked 解析 markdown
            let html;
            try {
                if (typeof marked !== 'undefined') {
                    html = marked.parse(content);
                } else {
                    html = content
                        .replace(/&/g, '&amp;')
                        .replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;')
                        .replace(/\n/g, '<br>');
                }
            } catch (e) {
                console.error('Markdown parse error:', e);
                html = content
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/\n/g, '<br>');
            }

            // 恢复数学公式并用 KaTeX 渲染
            for (let i = 0; i < mathStore.length; i++) {
                const { formula, display } = mathStore[i];
                const token = `${PLACEHOLDER_PREFIX}${i}MATH`;
                let rendered;
                try {
                    if (typeof katex !== 'undefined') {
                        rendered = katex.renderToString(formula, {
                            displayMode: display,
                            throwOnError: false,
                            output: 'html',
                        });
                    } else {
                        rendered = display ? `$$${formula}$$` : `$${formula}$`;
                    }
                } catch (e) {
                    rendered = display ? `$$${formula}$$` : `$${formula}$`;
                }
                // 占位符可能被 marked 包裹在 <p> 中，块级公式需跳出 <p>
                if (display) {
                    html = html.replace(
                        new RegExp(`<p>\\s*${token}\\s*</p>`, 'g'),
                        rendered
                    );
                }
                html = html.split(token).join(rendered);
            }

            return html;
        }

        // 格式化 token 数量（如 1.2k）
        function formatTokenCount(n) {
            if (n == null) return '0';
            if (n < 1000) return String(n);
            if (n < 10000) return (n / 1000).toFixed(1) + 'k';
            return (n / 1000).toFixed(0) + 'k';
        }

        // ===== 暗色模式 =====
        function toggleDarkMode() {
            state.darkMode = !state.darkMode;
            localStorage.setItem('darkMode', state.darkMode);
            document.documentElement.setAttribute('data-theme', state.darkMode ? 'dark' : 'light');
            showToast(state.darkMode ? '已切换到暗色模式' : '已切换到亮色模式', 'info');
        }

        function applyDarkMode() {
            document.documentElement.setAttribute('data-theme', state.darkMode ? 'dark' : 'light');
        }

        // ===== 消息操作 =====
        async function copyMessage(content) {
            try {
                await navigator.clipboard.writeText(content);
                showToast('已复制到剪贴板', 'success');
            } catch (e) {
                // 降级方案
                const textarea = document.createElement('textarea');
                textarea.value = content;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
                showToast('已复制', 'success');
            }
        }

        async function regenerateAnswer() {
            // 找到最后一条用户消息，重新发送
            if (state.isSending) return;
            let lastUserIndex = -1;
            for (let i = state.messages.length - 1; i >= 0; i--) {
                if (state.messages[i].role === 'user') {
                    lastUserIndex = i;
                    break;
                }
            }
            if (lastUserIndex === -1) {
                showToast('没有可重新生成的消息', 'error');
                return;
            }
            const lastUserMsg = state.messages[lastUserIndex];
            // 移除最后的助手回答
            state.messages = state.messages.slice(0, lastUserIndex + 1);
            // 重新发送
            await sendMessage(lastUserMsg.content);
        }

        // ===== 快捷提问 =====
        function useQuickPrompt(text) {
            state.inputMessage = text;
            adjustTextareaHeight();
            sendMessage();
        }

        // ===== 滚动控制 =====
        function scrollToBottom() {
            nextTick(() => {
                if (messagesContainer.value) {
                    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
                }
            });
        }

        function handleScroll() {
            if (!messagesContainer.value) return;
            const { scrollTop, scrollHeight, clientHeight } = messagesContainer.value;
            const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
            state.showScrollBottom = distanceFromBottom > 100;
            state.autoScroll = distanceFromBottom < 50;
        }

        // ===== 输入框自适应高度 =====
        function adjustTextareaHeight() {
            nextTick(() => {
                if (inputTextarea.value) {
                    inputTextarea.value.style.height = 'auto';
                    inputTextarea.value.style.height = Math.min(inputTextarea.value.scrollHeight, 150) + 'px';
                }
            });
        }

        function handleKeyDown(event) {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
            }
        }

        // ===== 模型管理 =====
        async function loadModels() {
            try {
                const response = await fetch(`${API_BASE_URL}/api/models`);
                const data = await response.json();
                // 优先使用后端返回的 models_detail（含 supports_image 等元信息）
                const rawModels = data.models_detail || data.models || [];
                state.models = rawModels.map(m => (typeof m === 'string' ? m : m.model_name));
                // 保存模型详细信息（包括是否支持图片）
                state.modelInfo = {};
                rawModels.forEach(m => {
                    if (typeof m === 'object') {
                        state.modelInfo[m.model_name] = m;
                    }
                });
                // 找到默认模型
                const defaultObj = rawModels.find(m => typeof m === 'object' && m.is_default);
                state.defaultModel = data.default_model || (defaultObj && defaultObj.model_name) || state.models[0] || 'deepseek-chat';
                if (!state.currentModel || !state.models.includes(state.currentModel)) {
                    state.currentModel = state.defaultModel;
                }
            } catch (error) {
                console.error('加载模型失败:', error);
                state.models = ['deepseek-chat', 'deepseek-reasoner', 'deepseek-coder'];
                state.currentModel = 'deepseek-chat';
            }
        }

        function switchModel() {
            showToast(`已切换到 ${formatModelName(state.currentModel)}`, 'info');
        }

        // ===== 深度思考 =====
        function toggleDeepThink() {
            state.deepThink = !state.deepThink;
            if (state.deepThink) {
                state.currentModel = 'deepseek-reasoner';
                showToast('🧠 深度思考模式已开启', 'info');
            } else {
                state.currentModel = 'deepseek-chat';
                showToast('深度思考模式已关闭', 'info');
            }
        }

        // ===== 停止生成 =====
        function stopGeneration() {
            if (state.abortController) {
                state.abortController.abort();
                state.abortController = null;
            }
            state.isSending = false;
            // 标记最后一条消息为已停止
            for (let i = state.messages.length - 1; i >= 0; i--) {
                if (state.messages[i].loading) {
                    state.messages[i].loading = false;
                    if (!state.messages[i].content) {
                        state.messages[i].content = '（已停止生成）';
                    }
                    break;
                }
            }
            showToast('已停止生成', 'info');
        }

        // ===== 预设管理 =====
        async function loadPresets() {
            try {
                const response = await fetch(`${API_BASE_URL}/api/presets`);
                const data = await response.json();
                // 适配新格式：presets 数组包含内置和自定义预设，每个元素有 {id, name, description, icon, system_prompt, is_builtin}
                state.presets = (data.presets || []).map(p => ({
                    id: p.id,
                    name: p.name || '未命名',
                    description: p.description || '',
                    system_prompt: p.system_prompt || '',
                    icon: p.icon || '🤖',
                    is_builtin: !!p.is_builtin,
                }));
                const defaultId = data.default || (state.presets[0] && state.presets[0].id);
                const defaultPreset = state.presets.find(p => p.id === defaultId) || state.presets[0];
                if (!state.currentPresetId && defaultPreset) {
                    state.currentPresetId = defaultPreset.id;
                    state.currentPreset = defaultPreset;
                } else if (state.currentPresetId) {
                    // 切换后保持当前选中预设为最新数据
                    const current = state.presets.find(p => p.id === state.currentPresetId);
                    if (current) state.currentPreset = current;
                }
            } catch (error) {
                console.error('加载预设失败:', error);
                state.presets = [{
                    id: 'kepu-assistant',
                    name: '科科',
                    description: '科普助手',
                    system_prompt: '',
                    icon: '🔬',
                    is_builtin: true,
                }];
                state.currentPresetId = 'kepu-assistant';
                state.currentPreset = state.presets[0];
            }
        }

        function switchPreset() {
            state.currentPreset = state.presets.find(p => p.id === state.currentPresetId) || null;
            if (state.currentPreset) {
                showToast(`${state.currentPreset.icon} ${state.currentPreset.name}`, 'info');
            }
        }

        // 打开预设列表弹窗
        function openPresetListModal() {
            state.showPresetModal = true;
            state.showPresetFormModal = false;
        }

        // 打开预设编辑/新建表单弹窗
        function openPresetModal(mode, preset) {
            if (!state.authToken) {
                showToast('请先登录后再管理预设', 'error');
                return;
            }
            state.presetModalMode = mode || 'create';
            if (mode === 'edit' && preset) {
                state.editingPreset = preset;
                state.presetForm = {
                    name: preset.name || '',
                    description: preset.description || '',
                    system_prompt: preset.system_prompt || '',
                    icon: preset.icon || '🤖',
                };
            } else {
                state.editingPreset = null;
                state.presetForm = { name: '', description: '', system_prompt: '', icon: '🤖' };
            }
            state.showPresetFormModal = true;
        }

        // 保存预设（新建或编辑）
        async function savePreset() {
            const form = state.presetForm;
            if (!form.name.trim()) {
                showToast('请输入预设名称', 'error');
                return;
            }
            if (!form.system_prompt.trim()) {
                showToast('请输入系统提示词', 'error');
                return;
            }
            const body = {
                name: form.name.trim(),
                description: form.description.trim(),
                system_prompt: form.system_prompt.trim(),
                icon: form.icon || '🤖',
            };
            try {
                let response;
                if (state.presetModalMode === 'edit' && state.editingPreset) {
                    response = await fetch(`${API_BASE_URL}/api/presets/${state.editingPreset.id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json', ...authHeaders() },
                        body: JSON.stringify(body),
                    });
                } else {
                    response = await fetch(`${API_BASE_URL}/api/presets`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', ...authHeaders() },
                        body: JSON.stringify(body),
                    });
                }
                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || '保存失败');
                }
                showToast(state.presetModalMode === 'edit' ? '预设已更新' : '预设已创建', 'success');
                state.showPresetFormModal = false;
                await loadPresets();
            } catch (error) {
                console.error('保存预设失败:', error);
                showToast(error.message || '保存预设失败', 'error');
            }
        }

        // 删除自定义预设
        async function deletePreset(preset) {
            if (preset.is_builtin) {
                showToast('内置预设不可删除', 'error');
                return;
            }
            if (!confirm(`确定删除预设「${preset.name}」吗？`)) return;
            try {
                const response = await fetch(`${API_BASE_URL}/api/presets/${preset.id}`, {
                    method: 'DELETE',
                    headers: { ...authHeaders() },
                });
                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || '删除失败');
                }
                // 如果删除的是当前选中的预设，切换到第一个
                if (state.currentPresetId === preset.id) {
                    state.currentPresetId = state.presets[0] && state.presets[0].id;
                    state.currentPreset = state.presets[0] || null;
                }
                showToast('预设已删除', 'success');
                await loadPresets();
            } catch (error) {
                console.error('删除预设失败:', error);
                showToast(error.message || '删除预设失败', 'error');
            }
        }

        // ===== 会话管理 =====
        async function loadSessions() {
            state.loadingSessions = true;
            try {
                let url = `${API_BASE_URL}/api/sessions?page=1&page_size=50`;
                if (state.searchKeyword.trim()) {
                    url += `&search=${encodeURIComponent(state.searchKeyword.trim())}`;
                }
                const response = await fetch(url, {
                    headers: { ...authHeaders() },
                });
                const data = await response.json();
                state.sessions = data.items || data.sessions || [];
            } catch (error) {
                console.error('加载会话列表失败:', error);
                showToast('加载会话列表失败', 'error');
            } finally {
                state.loadingSessions = false;
            }
        }

        function searchSessions() {
            if (state.searchTimer) clearTimeout(state.searchTimer);
            state.searchTimer = setTimeout(() => {
                loadSessions();
            }, 300);
        }

        function newSession() {
            state.currentSessionId = null;
            state.currentSessionTitle = '';
            state.messages = [];
            state.autoScroll = true;
            if (window.innerWidth <= 768) {
                state.sidebarOpen = false;
            }
        }

        async function selectSession(session) {
            if (state.editingSessionId === session.session_id) return;
            state.currentSessionId = session.session_id;
            state.currentSessionTitle = session.title;
            state.currentModel = session.model_name || state.currentModel;
            state.messages = [];
            state.autoScroll = true;
            if (window.innerWidth <= 768) {
                state.sidebarOpen = false;
            }
            await loadSessionMessages(session.session_id);
        }

        async function loadSessionMessages(sessionId) {
            state.loadingMessages = true;
            try {
                const response = await fetch(`${API_BASE_URL}/api/session/${sessionId}`, {
                    headers: { ...authHeaders() },
                });
                if (!response.ok) throw new Error('加载消息失败');
                const data = await response.json();
                const sessionData = data.session || data;
                const messages = sessionData.messages || data.messages || [];
                state.messages = messages.map(m => ({
                    role: m.role,
                    content: m.content,
                    image_url: m.image_data || null,
                    loading: false,
                    error: null,
                }));
                scrollToBottom();
            } catch (error) {
                console.error('加载消息失败:', error);
                showToast('加载消息失败', 'error');
            } finally {
                state.loadingMessages = false;
            }
        }

        async function deleteSession(session) {
            if (!confirm(`确定删除会话「${session.title || '新对话'}」吗？`)) return;
            try {
                const response = await fetch(`${API_BASE_URL}/api/session/${session.session_id}`, {
                    method: 'DELETE',
                    headers: { ...authHeaders() },
                });
                if (!response.ok) throw new Error('删除失败');
                if (state.currentSessionId === session.session_id) {
                    newSession();
                }
                await loadSessions();
                await loadStats();
                showToast('会话已删除', 'success');
            } catch (error) {
                console.error('删除会话失败:', error);
                showToast('删除会话失败', 'error');
            }
        }

        function startRename(session) {
            state.editingSessionId = session.session_id;
            state.editingTitle = session.title || '新对话';
            nextTick(() => {
                if (renameInput.value) {
                    // 重命名可能有多个，取最后一个
                    const inputs = document.querySelectorAll('.rename-input');
                    if (inputs.length > 0) {
                        const lastInput = inputs[inputs.length - 1];
                        lastInput.focus();
                        lastInput.select();
                    }
                }
            });
        }

        async function confirmRename() {
            if (state.editingSessionId === null) return;
            const sessionId = state.editingSessionId;
            const newTitle = state.editingTitle.trim();
            state.editingSessionId = null;
            if (!newTitle) return;

            try {
                const response = await fetch(`${API_BASE_URL}/api/session/${sessionId}/title`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        ...authHeaders(),
                    },
                    body: JSON.stringify({ title: newTitle }),
                });
                if (!response.ok) throw new Error('重命名失败');
                const data = await response.json();
                // 更新本地列表
                const session = state.sessions.find(s => s.session_id === sessionId);
                if (session) {
                    session.title = data.session ? data.session.title : data.title || newTitle;
                }
                if (state.currentSessionId === sessionId) {
                    state.currentSessionTitle = session ? session.title : newTitle;
                }
                showToast('已重命名', 'success');
            } catch (error) {
                console.error('重命名失败:', error);
                showToast('重命名失败', 'error');
            }
        }

        function cancelRename() {
            state.editingSessionId = null;
            state.editingTitle = '';
        }

        // ===== 统计 =====
        async function loadStats() {
            try {
                const response = await fetch(`${API_BASE_URL}/api/sessions/stats`, {
                    headers: { ...authHeaders() },
                });
                if (response.ok) {
                    const data = await response.json();
                    state.stats = data;
                    // 读取累计 token 统计（新增字段）
                    state.totalTokenUsage = {
                        prompt: data.total_prompt_tokens || 0,
                        completion: data.total_completion_tokens || 0,
                    };
                }
            } catch (error) {
                console.error('加载统计失败:', error);
            }
        }

        // ===== 聊天发送 =====
        async function sendMessage() {
            const message = state.inputMessage.trim();
            if ((!message && !state.pendingImage) || state.isSending) return;

            // Agent 模式走专用路径
            if (state.agentMode && message) {
                await sendAgentMessageFlow(message);
                return;
            }

            state.isSending = true;
            state.inputMessage = '';
            adjustTextareaHeight();

            // 重置本轮 token 统计
            state.lastTokenUsage = null;

            // 添加用户消息
            const userMessage = {
                role: 'user',
                content: message,
                image_url: state.pendingImage ? state.pendingImagePreview : null,
                loading: false,
                error: null,
            };
            state.messages.push(userMessage);
            scrollToBottom();

            // 添加助手加载消息
            const assistantIndex = state.messages.length;
            state.messages.push({
                role: 'assistant',
                content: '',
                loading: true,
                error: null,
            });
            scrollToBottom();

            // 准备图片数据
            let imageData = null;
            if (state.pendingImage) {
                imageData = state.pendingImagePreview; // 已是 base64 格式
            }
            const imagePreview = state.pendingImagePreview;
            state.pendingImage = null;
            state.pendingImagePreview = null;

            // 创建 AbortController 用于停止生成
            state.abortController = new AbortController();

            try {
                await sendStreamMessage(message || '请详细描述这张图片的内容。', assistantIndex, imageData);
                // 刷新会话列表（标题可能已更新）
                await loadSessions();
                await loadStats();
            } catch (error) {
                // 如果是用户主动中止，不显示错误
                if (error.name === 'AbortError') {
                    console.log('用户中止了请求');
                } else {
                    console.error('发送消息失败:', error);
                    state.messages[assistantIndex].loading = false;
                    state.messages[assistantIndex].error = error.message || '发送失败，请重试';
                }
            } finally {
                state.isSending = false;
                state.abortController = null;
            }
        }

        async function sendStreamMessage(message, assistantIndex, imageData = null) {
            const requestBody = {
                session_id: state.currentSessionId || null,
                message: message,
                model_name: state.currentModel || null,
                preset_id: state.currentPreset ? state.currentPreset.id : null,
            };
            if (imageData) {
                requestBody.image_data = imageData;
            }

            const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream',
                    ...authHeaders(),
                },
                body: JSON.stringify(requestBody),
                signal: state.abortController ? state.abortController.signal : undefined,
            });

            if (!response.ok) {
                const errorText = await response.text().catch(() => '');
                throw new Error(`请求失败 (${response.status}): ${errorText || response.statusText}`);
            }

            if (!response.body) {
                throw new Error('浏览器不支持流式响应');
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let fullContent = '';

            // 标记开始接收
            state.messages[assistantIndex].loading = false;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                // 按双换行分割完整的 SSE 事件
                const events = buffer.split('\n\n');
                buffer = events.pop(); // 保留最后一个不完整的片段

                for (const eventChunk of events) {
                    const lines = eventChunk.split('\n');
                    for (const line of lines) {
                        if (!line.startsWith('data:')) continue;
                        const dataStr = line.slice(5).trim();
                        if (!dataStr || dataStr === '[DONE]') continue;

                        try {
                            const data = JSON.parse(dataStr);

                            if (data.type === 'session') {
                                if (data.session_id) {
                                    state.currentSessionId = data.session_id;
                                }
                            } else if (data.type === 'chunk') {
                                fullContent += data.content || '';
                                state.messages[assistantIndex].content = fullContent;
                                if (state.autoScroll) {
                                    scrollToBottom();
                                }
                            } else if (data.type === 'usage') {
                                // 新增：处理 token 用量事件
                                const usage = data.usage || {};
                                state.lastTokenUsage = {
                                    prompt_tokens: usage.prompt_tokens || 0,
                                    completion_tokens: usage.completion_tokens || 0,
                                };
                                // 同步累计统计
                                state.totalTokenUsage.prompt += usage.prompt_tokens || 0;
                                state.totalTokenUsage.completion += usage.completion_tokens || 0;
                            } else if (data.type === 'done') {
                                state.messages[assistantIndex].loading = false;
                                if (data.session_id) {
                                    state.currentSessionId = data.session_id;
                                }
                            } else if (data.type === 'title') {
                                const session = state.sessions.find(s => s.session_id === state.currentSessionId);
                                if (session) {
                                    session.title = data.content;
                                }
                            } else if (data.type === 'error') {
                                state.messages[assistantIndex].loading = false;
                                state.messages[assistantIndex].error = data.content || data.message || '服务器错误';
                            }
                        } catch (parseError) {
                            console.warn('SSE 解析失败:', parseError, dataStr);
                        }
                    }
                }
            }

            // 处理缓冲区中剩余的数据
            if (buffer.trim()) {
                const lines = buffer.split('\n');
                for (const line of lines) {
                    if (!line.startsWith('data:')) continue;
                    const dataStr = line.slice(5).trim();
                    if (!dataStr || dataStr === '[DONE]') continue;
                    try {
                        const data = JSON.parse(dataStr);
                        if (data.type === 'chunk') {
                            fullContent += data.content || '';
                            state.messages[assistantIndex].content = fullContent;
                        } else if (data.type === 'done') {
                            state.messages[assistantIndex].loading = false;
                        } else if (data.type === 'error') {
                            state.messages[assistantIndex].loading = false;
                            state.messages[assistantIndex].error = data.content || data.message || '服务器错误';
                        }
                    } catch (parseError) {
                        // 忽略不完整的JSON
                    }
                }
            }

            // 确保加载状态关闭
            state.messages[assistantIndex].loading = false;

            if (!fullContent && !state.messages[assistantIndex].error) {
                state.messages[assistantIndex].error = '未收到回复内容';
            }
        }

        async function sendImageMessage(message, imageFile, assistantIndex) {
            // 将图片转为base64
            const base64Data = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => {
                    const result = reader.result;
                    // 去掉 data:image/xxx;base64, 前缀
                    const base64 = result.split(',')[1];
                    resolve(base64);
                };
                reader.onerror = reject;
                reader.readAsDataURL(imageFile);
            });

            const response = await fetch(`${API_BASE_URL}/api/chat/image`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...authHeaders(),
                },
                body: JSON.stringify({
                    session_id: state.currentSessionId || null,
                    message: message,
                    image_data: base64Data,
                    model_name: state.currentModel || null,
                    preset_id: state.currentPreset ? state.currentPreset.id : null,
                }),
            });

            if (!response.ok) {
                const errorText = await response.text().catch(() => '');
                throw new Error(`图片识别失败 (${response.status}): ${errorText || response.statusText}`);
            }

            const data = await response.json();
            state.messages[assistantIndex].loading = false;

            if (data.session_id) {
                state.currentSessionId = data.session_id;
            }
            state.messages[assistantIndex].content = data.response || data.content || '图片识别完成，但未返回内容。';
            scrollToBottom();
        }

        function sendSuggestion(text) {
            state.inputMessage = text;
            sendMessage();
        }

        // ===== 图片上传 =====
        function selectImage() {
            if (imageInput.value) {
                imageInput.value.click();
            }
        }

        function handleImageSelect(event) {
            const file = event.target.files && event.target.files[0];
            if (!file) return;

            if (!file.type.startsWith('image/')) {
                showToast('请选择图片文件', 'error');
                return;
            }

            if (file.size > 10 * 1024 * 1024) {
                showToast('图片大小不能超过10MB', 'error');
                return;
            }

            // 检查当前模型是否支持图片，不支持则自动切换
            const info = state.modelInfo[state.currentModel];
            if (info && info.supports_image === false) {
                // 找到支持图片的模型自动切换
                const imageModel = state.models.find(m => {
                    const mi = state.modelInfo[m];
                    return mi && mi.supports_image === true;
                });
                if (imageModel) {
                    state.currentModel = imageModel;
                    showToast(`已自动切换到 ${formatModelName(imageModel)} 以支持图片识别`, 'info');
                } else {
                    // 本地未发现视觉模型，仍允许上传，后端会自动回退到 qwen-vl-plus
                    showToast('当前模型不支持图片，后端将自动回退到视觉模型', 'info');
                }
            }

            state.pendingImage = file;
            const reader = new FileReader();
            reader.onload = (e) => {
                state.pendingImagePreview = e.target.result;
            };
            reader.readAsDataURL(file);

            // 重置 input 以便重复选择同一文件
            event.target.value = '';
        }

        function clearPendingImage() {
            state.pendingImage = null;
            state.pendingImagePreview = null;
        }

        // ===== 认证 =====
        function switchLoginMode(mode) {
            state.loginMode = mode;
        }

        async function submitAuth() {
            const username = state.loginUsername.trim();
            if (!username) {
                showToast('请输入昵称ID', 'error');
                return;
            }
            if (username.length < 2 || username.length > 32) {
                showToast('昵称ID长度需为 2-32 个字符', 'error');
                return;
            }

            state.loginLoading = true;
            try {
                const isRegister = state.loginMode === 'register';
                const url = isRegister
                    ? `${API_BASE_URL}/api/auth/register`
                    : `${API_BASE_URL}/api/auth/login`;
                const body = isRegister
                    ? { username, nickname: state.loginNickname.trim() || undefined }
                    : { username };

                const response = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.detail || '操作失败');
                }

                state.authToken = data.token;
                state.currentUser = data.user;
                localStorage.setItem('authToken', data.token);
                state.showLoginModal = false;
                state.loginUsername = '';
                state.loginNickname = '';
                showToast(`${isRegister ? '注册' : '登录'}成功！欢迎，${data.user.nickname || data.user.username}`, 'success');

                // 重新加载数据
                await loadSessions();
                await loadStats();
            } catch (error) {
                console.error('认证失败:', error);
                showToast(error.message || '操作失败', 'error');
            } finally {
                state.loginLoading = false;
            }
        }

        async function logout() {
            state.showUserMenu = false;
            try {
                await fetch(`${API_BASE_URL}/api/auth/logout`, {
                    method: 'POST',
                    headers: { ...authHeaders() },
                });
            } catch (error) {
                console.error('登出失败:', error);
            }

            state.authToken = null;
            state.currentUser = null;
            localStorage.removeItem('authToken');
            showToast('已退出登录', 'info');

            // 重新加载数据
            await loadSessions();
            await loadStats();
        }

        // 删除用户账号
        async function deleteAccount() {
            if (!state.authToken) {
                showToast('请先登录', 'error');
                return;
            }
            // 二次确认
            if (!confirm('⚠️ 确定要删除账号吗？此操作不可恢复，所有会话和自定义预设将永久丢失！')) return;
            if (!confirm('再次确认：真的要删除账号吗？此操作无法撤销！')) return;
            try {
                const response = await fetch(`${API_BASE_URL}/api/auth/user`, {
                    method: 'DELETE',
                    headers: {
                        'Content-Type': 'application/json',
                        ...authHeaders(),
                    },
                    body: JSON.stringify({ confirm: true }),
                });
                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || '删除账号失败');
                }
                // 清除 token、重置状态
                state.authToken = null;
                state.currentUser = null;
                localStorage.removeItem('authToken');
                state.showUserMenu = false;
                state.sessions = [];
                state.messages = [];
                state.currentSessionId = null;
                state.totalTokenUsage = { prompt: 0, completion: 0 };
                state.lastTokenUsage = null;
                showToast('账号已删除', 'success');
                await loadSessions();
                await loadStats();
                await loadPresets();
            } catch (error) {
                console.error('删除账号失败:', error);
                showToast(error.message || '删除账号失败', 'error');
            }
        }

        async function checkLoginStatus() {
            if (!state.authToken) return;
            try {
                const response = await fetch(`${API_BASE_URL}/api/auth/user/info`, {
                    headers: { ...authHeaders() },
                });
                if (response.ok) {
                    const data = await response.json();
                    state.currentUser = data.user;
                } else {
                    // token 失效
                    state.authToken = null;
                    localStorage.removeItem('authToken');
                }
            } catch (error) {
                console.error('检查登录状态失败:', error);
                state.authToken = null;
                localStorage.removeItem('authToken');
            }
        }

        // ===== 导出 =====
        async function exportSession(format) {
            state.showExportMenu = false;
            if (!state.currentSessionId) {
                showToast('请先选择一个会话', 'error');
                return;
            }

            try {
                // 后端支持 json 和 markdown，txt 使用 markdown 格式但保存为 .txt
                const backendFormat = format === 'txt' ? 'markdown' : format;
                const response = await fetch(
                    `${API_BASE_URL}/api/session/${state.currentSessionId}/export?format=${backendFormat}`,
                    { headers: { ...authHeaders() } }
                );

                if (!response.ok) {
                    throw new Error(`导出失败 (${response.status})`);
                }

                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;

                // 确定文件扩展名
                let ext = format;
                if (format === 'markdown') ext = 'md';
                a.download = `session_${state.currentSessionId.substring(0, 8)}.${ext}`;

                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);

                showToast(`已导出为 ${format.toUpperCase()} 格式`, 'success');
            } catch (error) {
                console.error('导出失败:', error);
                showToast(error.message || '导出失败', 'error');
            }
        }

        // ===== 全局点击关闭下拉菜单 =====
        function handleGlobalClick(event) {
            // 关闭导出菜单
            if (state.showExportMenu && !event.target.closest('.dropdown-wrapper')) {
                state.showExportMenu = false;
            }
            // 关闭用户菜单
            if (state.showUserMenu && !event.target.closest('.user-info')) {
                state.showUserMenu = false;
            }
        }

        // ===== 多模型并行对比 =====
        function openCompareModal() {
            // 默认选中当前模型
            state.compareModels = state.currentModel ? [state.currentModel] : [];
            state.compareResults = null;
            state.compareMessage = '';
            state.showCompareModal = true;
        }

        function toggleCompareModel(modelName) {
            const idx = state.compareModels.indexOf(modelName);
            if (idx >= 0) {
                state.compareModels.splice(idx, 1);
            } else {
                // 最多选 4 个
                if (state.compareModels.length >= 4) {
                    showToast('最多只能选择 4 个模型对比', 'error');
                    return;
                }
                state.compareModels.push(modelName);
            }
        }

        async function runCompare() {
            const msg = state.compareMessage.trim();
            if (!msg) {
                showToast('请输入要对比的问题', 'error');
                return;
            }
            if (state.compareModels.length < 2) {
                showToast('请至少选择 2 个模型进行对比', 'error');
                return;
            }
            state.compareLoading = true;
            state.compareResults = null;
            try {
                const response = await fetch(`${API_BASE_URL}/api/chat/compare`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...authHeaders(),
                    },
                    body: JSON.stringify({
                        message: msg,
                        model_names: state.compareModels,
                        preset_id: state.currentPreset ? state.currentPreset.id : null,
                    }),
                });
                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || '对比请求失败');
                }
                const data = await response.json();
                state.compareResults = data.results || [];
            } catch (error) {
                console.error('对比失败:', error);
                showToast(error.message || '对比失败', 'error');
            } finally {
                state.compareLoading = false;
            }
        }

        function closeCompareModal() {
            state.showCompareModal = false;
            state.compareResults = null;
            state.compareMessage = '';
            state.compareLoading = false;
        }

        // ===== 语音输入/输出 =====
        function initSpeechRecognition() {
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SR) {
                showToast('当前浏览器不支持语音识别', 'error');
                return null;
            }
            const recognition = new SR();
            recognition.lang = 'zh-CN';
            recognition.continuous = false;
            recognition.interimResults = true;
            let finalTranscript = '';
            recognition.onresult = (event) => {
                let interimText = '';
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    const transcript = event.results[i][0].transcript;
                    if (event.results[i].isFinal) {
                        finalTranscript += transcript;
                    } else {
                        interimText += transcript;
                    }
                }
                // 实时显示识别结果到输入框
                state.inputMessage = finalTranscript + interimText;
                adjustTextareaHeight();
            };
            recognition.onerror = (event) => {
                console.error('语音识别错误:', event.error);
                if (event.error === 'not-allowed') {
                    showToast('请允许浏览器使用麦克风', 'error');
                } else if (event.error !== 'aborted') {
                    showToast('语音识别出错: ' + event.error, 'error');
                }
                state.isListening = false;
            };
            recognition.onend = () => {
                state.isListening = false;
            };
            return recognition;
        }

        function toggleVoiceInput() {
            if (state.isListening) {
                // 停止
                if (state.speechRecognition) {
                    state.speechRecognition.stop();
                }
                state.isListening = false;
                return;
            }
            if (!state.speechRecognition) {
                state.speechRecognition = initSpeechRecognition();
                if (!state.speechRecognition) return;
            }
            try {
                state.speechRecognition.start();
                state.isListening = true;
                showToast('🎤 正在聆听...', 'info');
            } catch (error) {
                console.error('启动语音识别失败:', error);
                state.isListening = false;
                showToast('启动语音识别失败', 'error');
            }
        }

        // 朗读/停止朗读助手回答
        function toggleSpeech(content, msgIndex) {
            if (!('speechSynthesis' in window)) {
                showToast('当前浏览器不支持语音合成', 'error');
                return;
            }
            // 如果正在朗读同一条消息，停止
            if (state.isSpeaking && state.speakingMessageIndex === msgIndex) {
                window.speechSynthesis.cancel();
                state.isSpeaking = false;
                state.speakingMessageIndex = null;
                return;
            }
            // 停止之前的朗读
            if (state.isSpeaking) {
                window.speechSynthesis.cancel();
            }
            // 简单清理 markdown 标记
            const plainText = content
                .replace(/```[\s\S]*?```/g, '（代码块）')
                .replace(/[#*`_~\[\]>]/g, '')
                .replace(/\n+/g, '。')
                .trim();
            const utterance = new SpeechSynthesisUtterance(plainText);
            utterance.lang = 'zh-CN';
            utterance.rate = 1.0;
            utterance.onend = () => {
                state.isSpeaking = false;
                state.speakingMessageIndex = null;
            };
            utterance.onerror = () => {
                state.isSpeaking = false;
                state.speakingMessageIndex = null;
            };
            window.speechSynthesis.speak(utterance);
            state.isSpeaking = true;
            state.speakingMessageIndex = msgIndex;
        }

        // ===== 工具调用 Agent =====
        function toggleAgentMode() {
            state.agentMode = !state.agentMode;
            showToast(state.agentMode ? '🤖 Agent 模式已开启（支持工具调用）' : 'Agent 模式已关闭', 'info');
        }

        // Agent 发送消息流程（包装 sendMessage 的逻辑）
        async function sendAgentMessageFlow(message) {
            if (!state.authToken) {
                showToast('Agent 模式需要登录后使用', 'error');
                return;
            }
            state.isSending = true;
            state.inputMessage = '';
            adjustTextareaHeight();
            state.lastTokenUsage = null;

            // 添加用户消息
            state.messages.push({
                role: 'user',
                content: message,
                image_url: null,
                loading: false,
                error: null,
            });
            scrollToBottom();

            // 添加助手加载消息
            const assistantIndex = state.messages.length;
            state.messages.push({
                role: 'assistant',
                content: '',
                loading: true,
                error: null,
                toolCalls: [],
            });
            scrollToBottom();

            try {
                const response = await fetch(`${API_BASE_URL}/api/chat/agent`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...authHeaders(),
                    },
                    body: JSON.stringify({
                        message: message,
                        session_id: state.currentSessionId || null,
                        model_name: state.currentModel || null,
                        preset_id: state.currentPreset ? state.currentPreset.id : null,
                    }),
                });
                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || `请求失败 (${response.status})`);
                }
                const data = await response.json();
                state.messages[assistantIndex].loading = false;
                state.messages[assistantIndex].content = data.content || '（无回复）';
                if (data.tool_calls && data.tool_calls.length) {
                    state.messages[assistantIndex].toolCalls = data.tool_calls;
                }
                // 保存本轮 token 用量
                if (data.usage) {
                    state.lastTokenUsage = {
                        prompt_tokens: data.usage.prompt_tokens || 0,
                        completion_tokens: data.usage.completion_tokens || 0,
                    };
                    state.totalTokenUsage.prompt += data.usage.prompt_tokens || 0;
                    state.totalTokenUsage.completion += data.usage.completion_tokens || 0;
                }
                if (data.session_id) {
                    state.currentSessionId = data.session_id;
                }
                scrollToBottom();
                await loadSessions();
                await loadStats();
            } catch (error) {
                console.error('Agent 调用失败:', error);
                state.messages[assistantIndex].loading = false;
                state.messages[assistantIndex].error = error.message || 'Agent 调用失败';
            } finally {
                state.isSending = false;
            }
        }

        // ===== 生命周期 =====
        onMounted(async () => {
            document.addEventListener('click', handleGlobalClick);
            applyDarkMode();

            // 并行加载初始数据
            await Promise.all([
                loadModels(),
                loadPresets(),
            ]);

            // 设置默认预设
            if (state.presets.length > 0 && !state.currentPreset) {
                state.currentPresetId = state.presets[0].id;
                state.currentPreset = state.presets[0];
            }

            // 检查登录状态
            if (state.authToken) {
                await checkLoginStatus();
            }

            // 加载会话和统计
            await Promise.all([
                loadSessions(),
                loadStats(),
            ]);

            adjustTextareaHeight();
        });

        // ===== 监听消息变化自动滚动 =====
        watch(() => state.messages.length, () => {
            if (state.autoScroll) {
                scrollToBottom();
            }
        });

        // ===== 返回所有需要在模板中使用的 =====
        return {
            state,
            messagesContainer,
            inputTextarea,
            imageInput,
            renameInput,
            currentSessionTitle,
            // 工具
            formatModelName,
            formatTime,
            renderMarkdown,
            formatTokenCount,
            showToast,
            // 滚动
            scrollToBottom,
            handleScroll,
            // 输入
            adjustTextareaHeight,
            handleKeyDown,
            // 模型
            switchModel,
            // 预设
            switchPreset,
            openPresetListModal,
            openPresetModal,
            savePreset,
            deletePreset,
            // 会话
            loadSessions,
            searchSessions,
            newSession,
            selectSession,
            deleteSession,
            startRename,
            confirmRename,
            cancelRename,
            // 聊天
            sendMessage,
            sendSuggestion,
            // 图片
            selectImage,
            handleImageSelect,
            clearPendingImage,
            // 认证
            submitAuth,
            switchLoginMode,
            logout,
            deleteAccount,
            // 导出
            exportSession,
            // 新功能
            toggleDarkMode,
            copyMessage,
            regenerateAnswer,
            useQuickPrompt,
            toggleDeepThink,
            stopGeneration,
            // 多模型对比
            openCompareModal,
            toggleCompareModel,
            runCompare,
            closeCompareModal,
            // 语音
            toggleVoiceInput,
            toggleSpeech,
            // Agent
            toggleAgentMode,
        };
    }
});

app.mount('#app');
