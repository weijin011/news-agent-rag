<template>
  <div class="ai-chat-container">
    <van-nav-bar title="AI问答" fixed />

    <div class="mode-switch">
      <button
        v-for="m in modes"
        :key="m.value"
        :class="['mode-btn', mode === m.value ? 'active' : '']"
        @click="mode = m.value"
      >{{ m.label }}</button>
    </div>

    <div class="chat-content">
      <div class="messages-container" ref="messagesContainer">
        <div 
          v-for="(message, index) in messages" 
          :key="index" 
          :class="['message', message.role === 'user' ? 'user-message' : 'ai-message']"
        >
          <div class="message-content">
            <div v-if="message.role === 'assistant' && message.content === ''" class="typing-indicator">
              <span></span>
              <span></span>
              <span></span>
            </div>
            <div v-else v-html="formatMessage(message.content)"></div>
          </div>
          <div v-if="message.role === 'assistant' && message.trace?.length" class="tool-trace">
            <div class="trace-title">Agent 调用了工具</div>
            <div v-for="(t, ti) in message.trace" :key="ti" class="trace-item">
              <span class="trace-name">{{ t.name }}</span>
              <span class="trace-args">{{ JSON.stringify(t.arguments) }}</span>
              <span class="trace-count">{{ t.count }} 条</span>
            </div>
          </div>
          <div v-if="message.role === 'assistant' && message.sources?.length" class="source-list">
            <div class="source-title">引用来源（点击查看详情）</div>
            <a
              v-for="src in message.sources"
              :key="src.index"
              class="source-item"
              href="javascript:void(0)"
              @click="openSource(src)"
            >[{{ src.index }}] {{ src.title }}</a>
          </div>
        </div>
      </div>
      
      <div class="input-container">
        <van-field
          v-model="userInput"
          rows="1"
          autosize
          type="textarea"
          placeholder="请输入问题..."
          class="chat-input"
          @keypress.enter.prevent="sendMessage"
        />
        <van-button 
          type="primary" 
          class="send-button" 
          :disabled="isLoading || !userInput.trim()" 
          @click="sendMessage"
        >
          发送
        </van-button>
      </div>
    </div>
    
    <tab-bar />
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, watch } from 'vue';
import TabBar from '../components/TabBar.vue';
import * as marked from 'marked';
import DOMPurify from 'dompurify';
import { aiChatConfig } from '../config/api';
import { useRouter } from 'vue-router';

const router = useRouter();

// 两种模式：RAG 是固定检索管道，Agent 是模型自己选工具
const modes = [
  { value: 'rag', label: 'RAG问答' },
  { value: 'agent', label: 'Agent模式' },
];
const mode = ref('rag');

// 聊天消息：assistant 消息额外带 sources（由后端检索结果反查，不由模型生成）
const messages = ref([
  { role: 'assistant', content: '你好！我是AI助手，有什么可以帮助你的吗？', sources: [] }
]);
const userInput = ref('');
const messagesContainer = ref(null);
const isLoading = ref(false);
const sessionId = ref('');

// 只保留后端地址：密钥与模型名都由服务端 .env 决定，前端一律不接触
const apiEndpoint = ref(aiChatConfig.apiEndpoint);
const agentEndpoint = ref(aiChatConfig.agentEndpoint);

// 格式化消息内容（支持Markdown）
const formatMessage = (content) => {
  if (!content) return '';
  // 使用marked解析Markdown，并用DOMPurify清理HTML
  return DOMPurify.sanitize(marked.parse(content));
};

// 发送消息
const sendMessage = async () => {
  if (!userInput.value.trim() || isLoading.value) return;
  
  // 添加用户消息
  const userMessage = userInput.value.trim();
  messages.value.push({ role: 'user', content: userMessage });
  userInput.value = '';
  
  // 持久化用户消息
  persistMessage('user', userMessage);

  // 添加AI消息占位（引用与工具轨迹稍后由事件填入）
  messages.value.push({ role: 'assistant', content: '', sources: [], trace: [] });
  
  // 滚动到底部
  await nextTick();
  scrollToBottom();
  
  // 发送请求
  isLoading.value = true;
  try {
    await fetchAIResponse(userMessage);
  } catch (error) {
    console.error('Error fetching AI response:', error);
    // 更新最后一条消息为错误信息
    messages.value[messages.value.length - 1].content = `发生错误: ${error.message || '请检查网络连接和API设置'}`;
  } finally {
    isLoading.value = false;
    await nextTick();
    scrollToBottom();
  }
};

// 把一条消息写进持久化会话（失败不影响主流程）
const persistMessage = async (role, content, sources, trace) => {
  if (!sessionId.value) return;
  try {
    await fetch(`${aiChatConfig.apiBase}/sessions/${sessionId.value}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role, content, sources: sources || [], trace: trace || [] }),
    });
  } catch (e) {
    console.warn('消息持久化失败:', e);
  }
};

// 获取AI响应（使用SSE）
const fetchAIResponse = async (userMessage) => {
  const allMessages = messages.value
    .slice(0, -1) // 排除最后一个空的assistant消息
    .map(msg => ({ role: msg.role, content: msg.content }));
  
  try {
    const endpoint = mode.value === 'agent' ? agentEndpoint.value : apiEndpoint.value;
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        messages: allMessages
      })
    });
    
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error?.message || `HTTP error! status: ${response.status}`);
    }
    
    // 处理SSE流
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let aiResponse = '';
    // Agent 的工具调用轨迹：{name, arguments, count}
    const toolTrace = [];
  
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6);
        if (data === '[DONE]') continue;
        
        let json;
        try {
          json = JSON.parse(data);
        } catch (e) {
          console.error('Error parsing SSE data:', e);
          continue;
        }
        if (json.type === 'error') {
          throw new Error(json.message || '服务返回错误');
        }
        if (json.type === 'tool_call') {
          const call = { name: json.name, arguments: json.arguments || {}, count: 0 };
          toolTrace.push(call);
          messages.value[messages.value.length - 1].trace = [...toolTrace];
          await nextTick();
          scrollToBottom();
        } else if (json.type === 'tool_result') {
          const call = toolTrace[toolTrace.length - 1];
          if (call && call.name === json.name) {
            call.count = json.count || 0;
            messages.value[messages.value.length - 1].trace = [...toolTrace];
          }
        } else if (json.type === 'sources') {
          // 引用由后端检索结果反查，先于正文送达
          messages.value[messages.value.length - 1].sources = json.sources || [];
          await nextTick();
          scrollToBottom();
        } else if (json.type === 'delta') {
          const content = json.content || '';
          if (content) {
            aiResponse += content;
            messages.value[messages.value.length - 1].content = aiResponse;
            await nextTick();
            scrollToBottom();
          }
        }
      }
    }
  }
  
  // 如果没有收到任何内容
  if (!aiResponse) {
    messages.value[messages.value.length - 1].content = '抱歉，我无法生成回复。请检查API设置或稍后再试。';
  }
  // 持久化 assistant 消息（含引用与工具轨迹）
  const last = messages.value[messages.value.length - 1];
  persistMessage('assistant', last.content, last.sources, last.trace);
  } catch (error) {
    console.error('Fetch error:', error);
    throw error;
  }
};

// 点击引用跳到新闻详情页（news_id 来自检索元数据）
const openSource = (src) => {
  if (!src?.news_id) return;
  router.push({ name: 'NewsDetail', params: { id: src.news_id } });
};

// 滚动到底部
const scrollToBottom = () => {
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
  }
};

// 监听消息变化，自动滚动
watch(messages, () => {
  nextTick(scrollToBottom);
}, { deep: true });

// 组件挂载：读取持久化会话，刷新页面不丢历史
onMounted(async () => {
  try {
    const resp = await fetch(`${aiChatConfig.apiBase}/sessions/latest`);
    const data = await resp.json();
    sessionId.value = data.session_id || '';
    if (data.messages?.length) {
      messages.value = data.messages.map(m => ({
        role: m.role,
        content: m.content,
        sources: m.sources || [],
        trace: m.trace || [],
      }));
    }
  } catch (e) {
    console.warn('会话加载失败，使用临时会话:', e);
  }
  await nextTick();
  scrollToBottom();
});
</script>

<style scoped>
.mode-switch {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
  background: #fff;
  border-bottom: 1px solid #ebedf0;
}
.mode-btn {
  flex: 1;
  padding: 7px 0;
  border: 1px solid #dcdee0;
  border-radius: 6px;
  background: #f7f8fa;
  color: #646566;
  font-size: 13px;
}
.mode-btn.active {
  background: #1989fa;
  border-color: #1989fa;
  color: #fff;
}
.tool-trace {
  margin-top: 6px;
  padding: 8px 10px;
  background: #f0f5ff;
  border-radius: 8px;
  font-size: 12px;
}
.trace-title {
  color: #646566;
  margin-bottom: 5px;
}
.trace-item {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin: 4px 0;
}
.trace-name {
  color: #1989fa;
  font-weight: 500;
}
.trace-args {
  color: #646566;
  word-break: break-all;
}
.trace-count {
  color: #07c160;
}

.source-list {
  margin-top: 6px;
  padding: 8px 10px;
  background: #f7f8fa;
  border-radius: 8px;
  font-size: 12px;
}
.source-title {
  color: #969799;
  margin-bottom: 5px;
}
.source-item {
  display: block;
  color: #1989fa;
  text-decoration: none;
  line-height: 1.5;
  margin: 3px 0;
  word-break: break-all;
}

.ai-chat-container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  padding-top: 46px;
  padding-bottom: 50px;
  box-sizing: border-box;
}

.chat-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
}

.message {
  margin-bottom: 10px;
  max-width: 80%;
}

.user-message {
  margin-left: auto;
}

.ai-message {
  margin-right: auto;
}

.message-content {
  padding: 10px;
  border-radius: 10px;
  word-break: break-word;
}

.user-message .message-content {
  background-color: #007aff;
  color: white;
}

.ai-message .message-content {
  background-color: #f2f2f2;
  color: #333;
}

.input-container {
  display: flex;
  padding: 10px;
  border-top: 1px solid #eee;
  background-color: #fff;
}

.chat-input {
  flex: 1;
  margin-right: 10px;
}

.send-button {
  align-self: flex-end;
}

/* Markdown 样式 */
.message-content pre {
  background-color: #f8f8f8;
  padding: 10px;
  border-radius: 5px;
  overflow-x: auto;
}

.message-content code {
  background-color: rgba(0, 0, 0, 0.05);
  padding: 2px 4px;
  border-radius: 3px;
}

.message-content img {
  max-width: 100%;
}

/* 打字指示器 */
.typing-indicator {
  display: flex;
  padding: 5px;
}

.typing-indicator span {
  height: 8px;
  width: 8px;
  background-color: #999;
  border-radius: 50%;
  margin: 0 2px;
  display: inline-block;
  animation: bounce 1.5s infinite ease-in-out;
}

.typing-indicator span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes bounce {
  0%, 60%, 100% {
    transform: translateY(0);
  }
  30% {
    transform: translateY(-5px);
  }
}

/* Markdown样式 */
:deep(pre) {
  background-color: #f0f0f0;
  padding: 10px;
  border-radius: 4px;
  overflow-x: auto;
}

:deep(code) {
  font-family: monospace;
  background-color: #f0f0f0;
  padding: 2px 4px;
  border-radius: 4px;
}

:deep(p) {
  margin: 8px 0;
}

:deep(ul), :deep(ol) {
  padding-left: 20px;
}

:deep(a) {
  color: #1989fa;
  text-decoration: none;
}
</style>