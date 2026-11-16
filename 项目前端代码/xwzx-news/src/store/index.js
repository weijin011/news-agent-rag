import { createPinia } from 'pinia'
import { createPersistedState } from 'pinia-plugin-persistedstate'

const pinia = createPinia()

// 仅持久化用户偏好（上次选择的分类），其余运行时状态（列表/loading/categories 等）每次重新拉取，
// 避免 localStorage 里存旧数据导致首页永远显示空 tab。
pinia.use(createPersistedState({
  storage: localStorage,
  paths: ['news.currentCategory']
}))

export default pinia