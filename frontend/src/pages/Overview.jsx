import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const d = data.分布 || {}
  const k = data.口径 || {}
  const shift = data.班次
  return (
    <div className="panel">
      <h2>总览</h2>
      <p className="muted">
        当前班次：{shift ? `${shift.营业日} ${shift.班次名} · ${shift.状态}` : '无班次'}
      </p>
      <h3 className="muted">磅单状态分布</h3>
      <div className="grid">
        {Object.entries(d).map(([name, v]) => (
          <div className="stat" key={name}><div className="n">{v}</div><div className="l">{name}</div></div>
        ))}
      </div>
      <h3 className="muted">联动口径（与磅单册/地磅/写侧同一谓词）</h3>
      <div className="grid">
        <div className="stat"><div className="n">{k['待定级'] ?? 0}</div><div className="l">待定级桶</div></div>
        <div className="stat stat-missing"><div className="n">{k['其中缺品级'] ?? 0}</div><div className="l">其中缺品级（须定级）</div></div>
        <div className="stat"><div className="n">{k['其中已带品级'] ?? 0}</div><div className="l">其中已带品级</div></div>
        <div className="stat"><div className="n">{k['可结价'] ?? 0}</div><div className="l">可结价（待结价且已锁价）</div></div>
        <div className="stat"><div className="n">{k['在制占秤磅单'] ?? 0}</div><div className="l">在制占秤磅单</div></div>
      </div>
      {k['其中缺品级'] > 0 && (
        <div className="warn">
          有 {k['其中缺品级']} 张缺品级磅单停在待定级桶，未定级前结价口一律拒绝，不得当作可计价。
        </div>
      )}
      <div className="table-wrap">
        <table>
          <thead><tr><th>回收点</th><th>地磅</th><th>状态</th><th>回收点状态</th></tr></thead>
          <tbody>
            {(data.地磅 || []).map((c) => (
              <tr key={c.编号}><td>{c.回收点}</td><td>{c.地磅}</td><td>{c.状态}</td><td>{c.回收点状态}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
