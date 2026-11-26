'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  DashPage, DashPageHeader, Panel, StatusPill, Button, INPUT, buttonClass,
} from '@/design'
import { api, date, RecordData as D } from './api'

const actionNames: Record<string, string> = {
  material_read: '阅读资料', draft_save: '保存草稿', submission_attempt: '提交作答',
  grade_feedback: '评分反馈', practice_exercise: '完成练习',
  recheck_request: '申请复核', correction_revision: '修订评分',
}
const stateNames: Record<string, string> = {
  Good: '正确', Bad: '错误', Unknown: '尚不能确定', no_observation: '没有有效结果',
  correct: '正确', incorrect: '错误', partial: '部分完成',
}
const trendNames: Record<string, string> = {
  improving: '错误 → 正确', regressing: '正确 → 错误',
  fluctuating: '结果有变化', stable: '样例结果相同', insufficient_data: '记录不足',
}

export default function ResearchPreview({ role, courseId }: { role: string; courseId?: number }) {
  const [data, setData] = useState<D | null>(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState('scenario-1')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    setData(null)
    setError('')
    api('/research-preview').then((value) => {
      if (active) setData(value)
    }).catch((reason) => {
      if (active) setError(reason.message || '样例加载失败')
    })
    return () => { active = false }
  }, [revision])
  const scenario = data?.scenarios?.find((item: D) => item.id === selected)
  const coursePath = courseId ? `/education/teacher/courses/${courseId}` : '/education/teacher/courses'
  return (
    <DashPage>
      <DashPageHeader atlas={false} title="本版功能" subtitle="V2.1 阶段集成版 · 查看当前入口与画像规则示意。"
        breadcrumbs={[{ label: '教育工作台', href: `/education/${role}` }, { label: '本版功能', href: '#' }]} />
      <div className="edu-grid">
        <Panel padded className="edu-stack">
          <StatusPill status="info">按课程配置开放</StatusPill>
          <h2>教学资源生成</h2>
          <p>从课程资料准备候选练习，回看初稿与修订稿，由教师审核后发布。</p>
          <p className="edu-small edu-muted">生成方式以课程页面为准；模型方式需服务端配置与单次调用确认。</p>
          <Link className={buttonClass('secondary', 'sm')} href={role === 'teacher' ? (courseId ? `${coursePath}/generate` : coursePath) : '/education/admin/operations'}>
            {role === 'teacher' ? '打开练习准备' : '查看任务与能力'}
          </Link>
        </Panel>
        <Panel padded className="edu-stack">
          <StatusPill status="warning">合成预览</StatusPill>
          <h2>动态学习画像</h2>
          <p>下方可切换三组事件样例，查看记录顺序和规则计算的结果变化。</p>
          <p className="edu-small edu-muted">真实学习事件与画像版本入库仍在联调；课程内继续显示已有成绩统计。</p>
        </Panel>
        <Panel padded className="edu-stack">
          <StatusPill status="warning">结果展示已接入</StatusPill>
          <h2>自适应学习推荐</h2>
          <p>已接入经过校验的推荐结果展示与练习入口。暂无有效研究结果时，保留课程规则建议。</p>
          <p className="edu-small edu-muted">自动推荐任务尚未开放，画像和证据失效后不能继续使用旧结果。</p>
        </Panel>
        <Panel padded className="edu-stack">
          <StatusPill status="neutral">待接入</StatusPill>
          <h2>教学质量评估</h2>
          <p>下一轮接入序列质量评估与解释性反馈。当前成绩统计不代表研究效果已验证。</p>
        </Panel>
      </div>
      <Panel padded className="edu-stack">
        <div className="edu-inline justify-between">
          <h2>学习事件与结果变化</h2>
          <StatusPill status="warning">合成数据 · 只读</StatusPill>
        </div>
        <p>这些预设记录仅展示规则行为，不代表真实学生的掌握程度，也不会写入学生画像、推荐或成绩。</p>
        {error ? (
          <div role="alert" className="edu-stack">
            <p>{error}</p>
            <Button variant="secondary" onClick={() => setRevision((value) => value + 1)}>重新加载</Button>
          </div>
        ) : !data ? <p role="status">正在读取样例…</p> : (
          <>
            <label className="edu-field">
              <span>选择演示场景</span>
              <select className={INPUT} value={selected} onChange={(event) => setSelected(event.target.value)}>
                {data.scenarios.map((item: D) => <option key={item.id} value={item.id}>{item.title}</option>)}
              </select>
            </label>
            {scenario && <>
              <p className="edu-small edu-muted">共 {scenario.analysis.total_actions} 条样例事件。阅读与草稿只展示行为，不单独形成正确性判断。</p>
              <ol aria-label="样例事件顺序">
                {scenario.actions.map((action: D) => (
                  <li key={action.step_index} className="edu-list-row">
                    <div>
                      <strong>{action.step_index}. {actionNames[action.action_type] || action.action_type}</strong>
                      <p className="edu-small edu-muted">{date(action.timestamp)}</p>
                    </div>
                    <span>{stateNames[action.behavior_metrics.result] || '行为记录'}</span>
                  </li>
                ))}
              </ol>
              {Object.entries(scenario.analysis.concept_trajectories).map(([key, value]) => {
                const trajectory = value as D
                return <div key={key} className="edu-stack">
                  <h3>{trajectory.concept_name}</h3>
                  <p>最新样例结果：{stateNames[trajectory.current_state] || trajectory.current_state}；结果变化：{trendNames[trajectory.trend] || trajectory.trend}。</p>
                  <p className="edu-small edu-muted">{trajectory.limitations}</p>
                </div>
              })}
            </>}
          </>
        )}
      </Panel>
    </DashPage>
  )
}
