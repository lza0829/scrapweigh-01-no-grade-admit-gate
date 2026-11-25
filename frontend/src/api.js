export async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = { 明细: text } }
  if (!res.ok) {
    let msg = (data && (data.detail || data.明细)) || res.statusText
    // 后端结构化拒因 {拒因, 说明}：人读展示说明，机读码附在括号内，拒因不被吞掉
    if (msg && typeof msg === 'object' && msg.说明) {
      msg = msg.拒因 ? `[${msg.拒因}] ${msg.说明}` : msg.说明
    }
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data
}
