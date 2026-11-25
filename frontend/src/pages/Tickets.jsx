import React, { useEffect, useState } from 'react'
import { api } from '../api'

const STATUSES = ['', '待定级', '待结价', '已结价', '已销']
const MISSING_OPTS = [['', '全部品级'], ['1', '仅缺品级'], ['0', '仅已带品级']]

export default function Tickets() {
  const [rows, setRows] = useState([])
  const [status, setStatus] = useState('')
  const [missing, setMissing] = useState('')
  const [err, setErr] = useState('')
  const load = (s = status, m = missing) => {
    const qs = []
    if (s) qs.push(`状态=${encodeURIComponent(s)}`)
    if (m !== '') qs.push(`缺品级=${m}`)
    const q = qs.length ? `?${qs.join('&')}` : ''
    return api(`/磅条${q}`).then(setRows).catch((e) => setErr(e.message))
  }
  useEffect(() => { load('', '') }, [])
  return (
    <div className="panel">
      <h2>磅条册</h2>
      <div className="row">
        <label>状态
          <select value={status} onChange={(e) => { setStatus(e.target.value); load(e.target.value, missing) }}>
            {STATUSES.map((s) => <option key={s || '全部'} value={s}>{s || '全部'}</option>)}
          </select>
        </label>
        <label>品级事实
          <select value={missing} onChange={(e) => { setMissing(e.target.value); load(status, e.target.value) }}>
            {MISSING_OPTS.map(([v, l]) => <option key={v || 'all'} value={v}>{l}</option>)}
          </select>
        </label>
        <button type="button" className="secondary" onClick={() => load()}>刷新</button>
      </div>
      {err && <div className="err">{err}</div>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>磅条号</th><th>回收点</th><th>地磅</th><th>状态</th>
              <th>毛重</th><th>皮重</th><th>扣杂</th><th>净重</th>
              <th>品类</th><th>品级</th><th>缺品级</th><th>锁价</th><th>金额</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.缺品级 ? 'row-missing' : ''}>
                <td>{r.磅条号}</td><td>{r.回收点}</td><td>{r.地磅}</td><td>{r.状态}</td>
                <td>{r.毛重}</td><td>{r.皮重}</td><td>{r.扣杂}</td><td>{r.净重}</td>
                <td>{r.品类 || '-'}</td><td>{r.品级 || '-'}</td>
                <td>{r.缺品级 ? <span className="badge">缺品级</span> : <span className="badge ok">已带</span>}</td>
                <td>{r.锁价 ?? '-'}</td><td>{r.金额 ?? '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
