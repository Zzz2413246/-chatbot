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

            // 模型
            models: [],
            currentModel: 'deepseek-chat',
            defaultModel: 'deepseek-chat',

            // 预设
            presets: [],
            currentPresetId: '',
            currentPreset: null,

            // 认证
            authToken: localStorage.getItem('authToken') || null,
            currentUser: null,
            showLoginModal: false,
            loginCode: '',
            loginLoading: false,
            showUserMenu: false,
            wechatWebEnabled: false,
            qrLoginWindow: null,

            // UI
            sidebarOpen: window.innerWidth > 768,
            showExportMenu: false,
            showScrollBottom: false,
            autoScroll: true,

            // 统计
            stats: {
                total_sessions: 0,
                total_messages: 0,
                model_usage: {},
            },

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
            try {
                if (typeof marked !== 'undefined') {
                    return marked.parse(content);
                }
            } catch (e) {
                console.error('Markdown parse error:', e);
            }
            // 降级：简单转义
            return content
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/\n/g, '<br>');
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
                // 后端返回对象数组，提取 model_name 作为字符串列表
                const rawModels = data.models || [];
                state.models = rawModels.map(m => (typeof m === 'string' ? m : m.model_name));
                // 找到默认模型
                const defaultObj = rawModels.find(m => typeof m === 'object' && m.is_default);
                state.defaultModel = data.default_model || (defaultObj && defaultObj.model_name) || state.models[0] || 'deepseek-chat';
                if (!state.currentModel || !state.models.includes(state.currentModel)) {
                    state.currentModel = state.defaultModel;
                }
            } catch (error) {
                console.error('加载模型失败:', error);
                state.models = ['deepseek-chat', 'deepseek-coder', 'gpt-4o-mini'];
                state.currentModel = 'deepseek-chat';
            }
        }

        function switchModel() {
            showToast(`已切换到 ${formatModelName(state.currentModel)}`, 'info');
        }

        // ===== 预设管理 =====
        async function loadPresets() {
            try {
                const response = await fetch(`${API_BASE_URL}/api/presets`);
                const data = await response.json();
                state.presets = data.presets || [];
                const defaultId = data.default || (state.presets[0] && state.presets[0].id);
                const defaultPreset = state.presets.find(p => p.id === defaultId) || state.presets[0];
                if (!state.currentPresetId && defaultPreset) {
                    state.currentPresetId = defaultPreset.id;
                    state.currentPreset = defaultPreset;
                }
            } catch (error) {
                console.error('加载预设失败:', error);
                state.presets = [{
                    id: 'kepu-assistant',
                    name: '科科',
                    description: '科普助手',
                    system_prompt: '',
                    icon: '🔬',
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
                    state.stats = await response.json();
                }
            } catch (error) {
                console.error('加载统计失败:', error);
            }
        }

        // ===== 聊天发送 =====
        async function sendMessage() {
            const message = state.inputMessage.trim();
            if ((!message && !state.pendingImage) || state.isSending) return;

            state.isSending = true;
            state.inputMessage = '';
            adjustTextareaHeight();

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

            const imageFile = state.pendingImage;
            const imagePreview = state.pendingImagePreview;
            state.pendingImage = null;
            state.pendingImagePreview = null;

            try {
                if (imageFile) {
                    await sendImageMessage(message || '请详细描述这张图片的内容。', imageFile, assistantIndex);
                } else {
                    await sendStreamMessage(message, assistantIndex);
                }
                // 刷新会话列表（标题可能已更新）
                await loadSessions();
                await loadStats();
            } catch (error) {
                console.error('发送消息失败:', error);
                state.messages[assistantIndex].loading = false;
                state.messages[assistantIndex].error = error.message || '发送失败，请重试';
            } finally {
                state.isSending = false;
            }
        }

        async function sendStreamMessage(message, assistantIndex) {
            const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream',
                    ...authHeaders(),
                },
                body: JSON.stringify({
                    session_id: state.currentSessionId || null,
                    message: message,
                    model_name: state.currentModel || null,
                    preset_id: state.currentPreset ? state.currentPreset.id : null,
                }),
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
                                // 获取会话ID
                                if (data.session_id) {
                                    state.currentSessionId = data.session_id;
                                }
                            } else if (data.type === 'chunk') {
                                // 追加内容
                                fullContent += data.content || '';
                                state.messages[assistantIndex].content = fullContent;
                                if (state.autoScroll) {
                                    scrollToBottom();
                                }
                            } else if (data.type === 'done') {
                                // 完成
                                state.messages[assistantIndex].loading = false;
                                if (data.session_id) {
                                    state.currentSessionId = data.session_id;
                                }
                            } else if (data.type === 'title') {
                                // 标题自动生成
                                // 更新会话列表中的标题
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
        async function loadWechatStatus() {
            try {
                const response = await fetch(`${API_BASE_URL}/api/auth/wechat/status`);
                if (response.ok) {
                    const data = await response.json();
                    state.wechatWebEnabled = data.web_login_enabled;
                }
            } catch (error) {
                console.error('检查微信登录状态失败:', error);
            }
        }

        async function startWechatQrLogin() {
            try {
                const response = await fetch(`${API_BASE_URL}/api/auth/wechat/qrurl`);
                if (!response.ok) {
                    const err = await response.json().catch(() => ({}));
                    throw new Error(err.detail || '获取扫码登录链接失败');
                }
                const data = await response.json();
                // 在新窗口打开微信扫码登录页
                state.qrLoginWindow = window.open(data.qr_url, 'wechat_login', 'width=600,height=600');
                state.showLoginModal = false;
                showToast('请在弹出的窗口中扫码登录', 'info');
            } catch (error) {
                console.error('扫码登录失败:', error);
                showToast(error.message || '扫码登录失败', 'error');
            }
        }

        function handleWechatMessage(event) {
            // 接收来自微信回调页面的登录成功消息
            if (event.data && event.data.type === 'wechat_login_success') {
                state.authToken = event.data.token;
                state.currentUser = event.data.user;
                localStorage.setItem('authToken', event.data.token);
                showToast(`欢迎，${event.data.user.nickname || '微信用户'}！`, 'success');
                state.qrLoginWindow = null;
                // 重新加载数据
                loadSessions();
                loadStats();
            }
        }

        async function login() {
            const code = state.loginCode.trim() || 'mock_code_' + Date.now();
            state.loginLoading = true;
            try {
                const response = await fetch(`${API_BASE_URL}/api/auth/wechat/login`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ code }),
                });

                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.detail || `登录失败 (${response.status})`);
                }

                const data = await response.json();
                state.authToken = data.token;
                state.currentUser = data.user;
                localStorage.setItem('authToken', data.token);
                state.showLoginModal = false;
                state.loginCode = '';

                showToast(`欢迎，${state.currentUser.nickname || '用户'}！`, 'success');

                // 重新加载数据
                await loadSessions();
                await loadStats();
            } catch (error) {
                console.error('登录失败:', error);
                showToast(error.message || '登录失败，请重试', 'error');
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

        // ===== 生命周期 =====
        onMounted(async () => {
            document.addEventListener('click', handleGlobalClick);
            window.addEventListener('message', handleWechatMessage);

            // 并行加载初始数据
            await Promise.all([
                loadModels(),
                loadPresets(),
                loadWechatStatus(),
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
            login,
            logout,
            startWechatQrLogin,
            // 导出
            exportSession,
        };
    }
});

app.mount('#app');
