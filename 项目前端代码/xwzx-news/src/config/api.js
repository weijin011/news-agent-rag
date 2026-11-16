/**
 * API 配置
 *
 * 重要：前端不再保存任何模型密钥。
 * 原先硬编码在此处的 key 已经暴露在浏览器里，务必到模型控制台重置。
 * 现在统一请求自己的后端 /api/ai/chat，密钥由服务端 .env 保管。
 */

export const apiConfig = {
  baseURL: 'http://127.0.0.1:8000',
}

export const aiChatConfig = {
  apiBase: `${apiConfig.baseURL}/api`,
  apiEndpoint: `${apiConfig.baseURL}/api/ai/chat`,
  agentEndpoint: `${apiConfig.baseURL}/api/ai/agent-chat`,
}
