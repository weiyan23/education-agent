'use client'

import React, { useEffect, useState, useCallback, useId } from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import {
  GraduationCap,
  House,
  Books,
  ClipboardText,
  ChartLineUp,
  ShieldCheck,
  UsersThree,
  GearSix,
  SignOut,
  BookOpenText,
  CheckCircle,
  WarningCircle,
  Plus,
  Clock,
  Files,
  ListChecks,
  UserCircle,
  LockSimple,
  NotePencil,
  ChartBar,
  Info,
  ArrowClockwise,
} from '@phosphor-icons/react'
import {
  DashPage,
  DashPageHeader,
  Panel,
  Button,
  StatCard,
  EmptyState,
  StatusPill,
  Skeleton,
  INPUT,
  TEXTAREA,
  buttonClass,
} from '@/design'
import { api, ApiError, RecordData as D, items, date, label } from './api'
import './education.css'

const base = '/education'
const roles: Record<string, string> = {
  student: '学生',
  teacher: '教师',
  admin: '管理员',
  administrator: '管理员',
}
const capabilityNames: Record<string, string> = {
  research_generator: '多智能体教学资源生成',
  ai_evaluation: '智能评价与反馈',
  research_profile: '动态学习画像',
  research_recommendation: '自适应学习推荐',
  course_qa: '课程智能问答',
  code_execution: '代码沙箱评测',
}
const operationNames: Record<string, string> = {
  generate: '练习准备',
  projection: '学习统计更新',
  evaluate: '作答评价',
  profile: '学习画像更新',
  recommend: '学习推荐更新',
}
const pathFor = (role: string, p = '') =>
  `${base}/${role === 'administrator' ? 'admin' : role}${p}`
function useRemote(path: string | null) {
  const [data, setData] = useState<D | null>(null),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(!!path),
    [revision, setRevision] = useState(0)
  const reload = useCallback(() => setRevision((n) => n + 1), [])
  useEffect(() => {
    let active = true
    if (!path) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError('')
    api(path)
      .then((d) => {
        if (active) setData(d)
      })
      .catch((e) => {
        if (active) setError(e.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [path, revision])
  return { data, setData, error, loading, reload }
}
function Notice({
  children,
  error = false,
}: {
  children: React.ReactNode
  error?: boolean
}) {
  return (
    <div
      role={error ? 'alert' : 'status'}
      className={`edu-notice ${error ? 'edu-error' : ''}`}
    >
      <Info size={20} className="shrink-0 mt-0.5" />
      <span>{children}</span>
    </div>
  )
}
function Field({
  label: caption,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  const fieldId = useId()
  return (
    <label className="edu-field">
      <span id={fieldId}>{caption}</span>
      {React.isValidElement(children)
        ? React.cloneElement(
            children as React.ReactElement<{ 'aria-labelledby'?: string }>,
            { 'aria-labelledby': fieldId }
          )
        : children}
    </label>
  )
}
function Loading() {
  return (
    <div className="edu-loading">
      <Skeleton className="h-8 w-60" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  )
}
function Blank({
  title = '暂无内容',
  description = '内容准备好后会显示在这里。',
  action,
}: {
  title?: string
  description?: string
  action?: React.ReactNode
}) {
  return (
    <Panel>
      <EmptyState
        icon={<Files size={25} />}
        title={title}
        description={description}
        action={action}
      />
    </Panel>
  )
}
function Tag({ value }: { value: any }) {
  return (
    <StatusPill
      className="!text-sm"
      status={
        [
          'published',
          'publish',
          'pass',
          'approve',
          'succeeded',
          'effective',
          'ready',
          'scored',
        ].includes(value)
          ? 'success'
          : ['failed', 'reject', 'fail'].includes(value)
            ? 'danger'
            : ['needs_review', 'pending', 'queued', 'open', 'stale'].includes(
                  value
                )
              ? 'warning'
              : 'neutral'
      }
    >
      {value === 'publish' ? '已发布' : label(value)}
    </StatusPill>
  )
}
function Page({
  title,
  subtitle,
  children,
  actions,
}: {
  title: string
  subtitle?: string
  children: React.ReactNode
  actions?: React.ReactNode
}) {
  return (
    <DashPage>
      <DashPageHeader
        atlas={false}
        title={title}
        subtitle={subtitle}
        breadcrumbs={[
          { label: '教育工作台', href: base },
          { label: title, href: '#' },
        ]}
        actions={actions}
      />
      {children}
    </DashPage>
  )
}
function AsyncContent({
  state,
  children,
}: {
  state: ReturnType<typeof useRemote>
  children: (d: D) => React.ReactNode
}) {
  if (state.loading && !state.data) return <Loading />
  if (state.error)
    return (
      <Blank
        title="暂时无法加载"
        description={state.error}
        action={
          <Button onClick={state.reload} icon={<ArrowClockwise />}>
            重新加载
          </Button>
        }
      />
    )
  if (!state.data) return <Loading />
  return <>{children(state.data)}</>
}
function Brand() {
  return (
    <div className="edu-brand">
      <div className="edu-brand-mark">
        <GraduationCap size={25} weight="bold" />
      </div>
      <div>
        <strong>知学</strong>
        <small>EDUCATION AGENT</small>
      </div>
    </div>
  )
}
function Login({ onLogin }: { onLogin: () => void }) {
  const [email, setEmail] = useState(''),
    [password, setPassword] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api('/api/v1/auth/login', {
        method: 'POST',
        form: true,
        body: { username: email, password, org_slug: 'default' },
      })
      setPassword('')
      onLogin()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="edu-app edu-login">
      <section className="edu-login-main">
        <Brand />
        <form className="edu-login-form edu-form" onSubmit={submit}>
          <div>
            <h1>开始今天的教与学</h1>
            <p className="edu-muted">登录你的账号，继续课程与学习任务。</p>
          </div>
          {error && <Notice error>{error}</Notice>}
          <Field label="账号邮箱">
            <input
              className={INPUT}
              type="email"
              value={email}
              autoComplete="username"
              onChange={(e) => setEmail(e.target.value)}
              placeholder="请输入账号邮箱"
              required
            />
          </Field>
          <Field label="密码">
            <input
              className={INPUT}
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              placeholder="请输入密码"
              required
            />
          </Field>
          <Button
            type="submit"
            variant="primary"
            loading={busy}
            className="!h-12 !text-base"
          >
            登录
          </Button>
          <p className="edu-small edu-muted">
            演示账号与初始密码由本地初始化工具生成，请使用交付说明中的账号信息。
          </p>
        </form>
        <p className="edu-footer">学生 · 教师 · 管理员 / 本地演示版</p>
      </section>
      <aside className="edu-login-aside">
        <h2>
          让每一次学习，
          <br />
          都有清晰的下一步。
        </h2>
        <p>连接课程、作答与反馈，让教学过程可追溯，让学习计划更有依据。</p>
        <div className="edu-login-steps">
          <div className="edu-login-step">
            <Books size={28} />
            <span>组织课程，准备适合的学习内容</span>
          </div>
          <div className="edu-login-step">
            <NotePencil size={28} />
            <span>完成练习，保留每一次尝试</span>
          </div>
          <div className="edu-login-step">
            <ChartLineUp size={28} />
            <span>查看反馈，继续有目标的学习</span>
          </div>
        </div>
        <div className="edu-login-orbit" />
      </aside>
    </div>
  )
}

export default function EducationApp() {
  const pathname = usePathname(),
    router = useRouter(),
    [session, setSession] = useState<D | null>(null),
    [ready, setReady] = useState(false),
    [toast, setToast] = useState(''),
    [sessionRevision, setSessionRevision] = useState(0)
  const parts = pathname
      .replace(/^\/education\/?/, '')
      .split('/')
      .filter(Boolean),
    role = parts[0] || '',
    isLogin = role === 'login'
  const courses = useRemote(
    session && !session.principal.role_views.includes('administrator')
      ? '/courses'
      : null
  )
  useEffect(() => {
    let active = true
    api('/session')
      .then((s) => {
        if (!active) return
        setSession(s)
        if (isLogin || parts.length === 0)
          router.replace(pathFor(s.principal.role_views[0]))
      })
      .catch(() => {
        if (active) {
          setSession(null)
          router.replace(base + '/login')
        }
      })
      .finally(() => {
        if (active) setReady(true)
      })
    return () => {
      active = false
    }
  }, [sessionRevision]) // Session identity is always server-owned.
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(''), 4500)
      return () => clearTimeout(t)
    }
  }, [toast])
  useEffect(() => {
    const f = () => {
      setSession(null)
      router.replace(base + '/login')
    }
    window.addEventListener('education-session-expired', f)
    return () => window.removeEventListener('education-session-expired', f)
  }, [router])
  const refreshSession = () => {
    setReady(false)
    setSessionRevision((v) => v + 1)
  }
  if (!ready)
    return (
      <div className="edu-app">
        <Loading />
      </div>
    )
  if (isLogin || !session) return <Login onLogin={refreshSession} />
  const actualRole = session.principal.role_views.includes('administrator')
    ? 'admin'
    : session.principal.role_views[0]
  const allowed = role === actualRole || role === '' || role === 'account'
  const courseId = items(courses.data)[0]?.id,
    home = pathFor(actualRole)
  const nav =
    actualRole === 'admin'
      ? [
          ['', '管理概览', House],
          ['/accounts', '账号管理', UsersThree],
          ['/access', '权限范围', ShieldCheck],
          ['/policies', '教学策略', GearSix],
          ['/operations', '任务与能力', ListChecks],
          ['/audit', '操作记录', ClipboardText],
        ]
      : actualRole === 'teacher'
        ? [
            ['', '教学首页', House],
            ['/courses', '我的课程', Books],
            [
              courseId ? `/courses/${courseId}/generate` : '/courses',
              '练习准备',
              NotePencil,
            ],
            [
              courseId ? `/courses/${courseId}/assignments` : '/courses',
              '作业与批阅',
              ClipboardText,
            ],
            [
              courseId ? `/courses/${courseId}/learners` : '/courses',
              '学生学习状态',
              ChartLineUp,
            ],
          ]
        : [
            ['', '学习首页', House],
            ['/courses', '我的课程', Books],
            [
              courseId ? `/courses/${courseId}/assignments` : '/courses',
              '我的作业',
              ClipboardText,
            ],
            [
              courseId ? `/courses/${courseId}/state` : '/courses',
              '学习状态',
              ChartLineUp,
            ],
          ]
  async function logout() {
    try {
      await api('/api/v1/auth/logout', { method: 'DELETE' })
      setSession(null)
      router.replace(base + '/login')
    } catch (e) {
      setToast((e as Error).message)
    }
  }
  return (
    <div className="edu-app">
      <aside className="edu-sidebar">
        <Link href={home}>
          <Brand />
        </Link>
        <p className="edu-nav-label">
          {actualRole === 'student'
            ? '学习空间'
            : actualRole === 'teacher'
              ? '教学空间'
              : '系统管理'}
        </p>
        <nav>
          {nav.map(([p, name, Icon]: any) => (
            <Link
              key={name}
              className={`edu-nav-link ${pathname === home + p ? 'active' : ''}`}
              href={home + p}
            >
              <Icon size={21} />
              {name}
            </Link>
          ))}
        </nav>
        <div className="edu-sidebar-end">
          <Link className="edu-nav-link" href={home + '/account'}>
            <UserCircle size={21} />
            我的账号
          </Link>
          <div className="edu-user">
            <div className="edu-avatar">
              {String(session.principal.display_name).slice(0, 1)}
            </div>
            <div>
              <p className="edu-small font-semibold">
                {session.principal.display_name}
              </p>
              <p className="edu-small edu-muted">{roles[actualRole]}账号</p>
            </div>
          </div>
          <Button
            variant="ghost"
            onClick={logout}
            icon={<SignOut size={18} />}
            className="w-full"
          >
            退出登录
          </Button>
        </div>
      </aside>
      <main className="edu-main">
        <header className="edu-topbar">
          <p>
            {roles[actualRole]}工作台 <span className="mx-3">/</span>{' '}
            <span>有依据的教与学</span>
          </p>
          <div className="edu-inline">
            <StatusPill status="neutral">本地演示</StatusPill>
            <Button variant="ghost" size="sm" onClick={logout}>
              退出
            </Button>
          </div>
        </header>
        <div className="edu-body">
          {!allowed ? (
            <Page title="无权访问">
              <Notice error>当前账号没有这个角色的权限。</Notice>
              <Link className={buttonClass('secondary')} href={home}>
                返回工作台
              </Link>
            </Page>
          ) : (
            <Workspace
              key={pathname}
              parts={parts}
              role={actualRole}
              session={session}
              courses={courses}
              notify={setToast}
            />
          )}
        </div>
        <footer className="edu-footer">
          知学 · 教育智能体 V2　/　课程数据按账号授权展示
        </footer>
      </main>
      {toast && (
        <div role="status" className="edu-toast">
          {toast}
        </div>
      )}
    </div>
  )
}

type WorkspaceProps = {
  parts: string[]
  role: string
  session: D
  courses: ReturnType<typeof useRemote>
  notify: (s: string) => void
}
function Workspace(props: WorkspaceProps) {
  const { parts, role, session, courses, notify } = props,
    sub = parts[1],
    id = parts[2],
    tab = parts[3] || 'overview'
  if (sub === 'account') return <Account session={session} />
  if (role === 'admin')
    return (
      <Admin section={sub || 'overview'} notify={notify} session={session} />
    )
  if (role === 'student' && sub === 'recommended') {
    const [, , course, student, decision, binding] = parts
    if (parts.length !== 6 || !/^\d+$/.test(course) ||
        student !== String(session.principal.user_id) ||
        ![decision, binding].every(value => /^[0-9a-f-]{36}$/i.test(value)))
      return <Notice error>推荐入口无效，请返回课程重新查看建议。</Notice>
    const source = `/courses/${course}/students/${student}/recommendations/${decision}/exercises/${binding}`
    return <Binding role={role} id={binding} notify={notify} source={source} />
  }
  if (sub === 'courses' && id)
    return (
      <Course role={role} id={id} tab={tab} session={session} notify={notify} />
    )
  if (sub === 'resources' && id)
    return <Resource role={role} id={id} notify={notify} />
  if (sub === 'bindings' && id)
    return <Binding role={role} id={id} notify={notify} />
  if (sub === 'attempts' && id) return <Attempt id={id} notify={notify} />
  if (sub === 'submissions' && id)
    return <Submission role={role} id={id} notify={notify} />
  if (sub === 'tasks' && id) return <Task id={id} />
  return (
    <Home
      role={role}
      session={session}
      courses={courses}
      onlyCourses={sub === 'courses'}
      notify={notify}
    />
  )
}
function Account({ session }: { session: D }) {
  return (
    <Page title="我的账号" subtitle="身份和授权范围由服务端管理。">
      <Panel padded className="edu-stack">
        <div className="edu-inline">
          <div className="edu-avatar">
            {session.principal.display_name.slice(0, 1)}
          </div>
          <div>
            <h2>{session.principal.display_name}</h2>
            <p className="edu-muted">
              {session.principal.role_views
                .map((v: string) => roles[v])
                .join('、')}
            </p>
          </div>
        </div>
        <div className="edu-meta">
          <span>组织编号 {session.principal.org_id}</span>
          <span>账号编号 {session.principal.user_id}</span>
        </div>
        <Notice>角色和课程范围由管理员授予。页面切换不会增加账号权限。</Notice>
      </Panel>
      <Panel padded>
        <h2 className="mb-4">当前授权</h2>
        {session.scopes?.length ? (
          session.scopes.map((s: D, i: number) => (
            <div key={i} className="edu-list-row">
              <span>{s.action || s.actions?.join('、') || '课程范围'}</span>
              <span className="edu-muted edu-small">
                课程 {s.course_id ?? '组织范围'}
                {s.usergroup_id ? ` · 班级 ${s.usergroup_id}` : ''}
              </span>
            </div>
          ))
        ) : (
          <p className="edu-muted">按当前组织和班级成员关系读取本人内容。</p>
        )}
      </Panel>
    </Page>
  )
}
function Home({
  role,
  session,
  courses,
  onlyCourses,
  notify,
}: {
  role: string
  session: D
  courses: ReturnType<typeof useRemote>
  onlyCourses: boolean
  notify: (s: string) => void
}) {
  return (
    <Page
      title={
        onlyCourses
          ? '我的课程'
          : role === 'teacher'
            ? `你好，${session.principal.display_name}`
            : '今天，从一个小目标开始'
      }
      subtitle={
        onlyCourses
          ? '在这里查看当前有权访问的课程。'
          : role === 'teacher'
            ? '准备教学内容，查看作答，给出有依据的反馈。'
            : '继续你的课程，完成练习，把反馈变成下一步。'
      }
      actions={
        role === 'teacher' ? (
          <Button
            icon={<Plus />}
            disabled
            title="本版课程由初始化工具准备，暂未开放新建"
          >
            新建课程
          </Button>
        ) : undefined
      }
    >
      <AsyncContent state={courses}>
        {(data) => (
          <>
            {!onlyCourses && (
              <Panel className="edu-hero">
                <div>
                  <p className="edu-small">
                    {role === 'teacher' ? '课程工作台' : '学习计划'}
                  </p>
                  <h2>
                    {role === 'teacher'
                      ? '从课程内容到学习反馈'
                      : '每一步，都有记录'}
                  </h2>
                  <p>
                    {role === 'teacher'
                      ? '为班级准备练习，检查内容后发布。学生提交后，可以查看评分、处理复核，并跟进学习状态。'
                      : '课程资料、待办作业和反馈保存在同一个学习空间。你可以随时回到自己的提交记录。'}
                  </p>
                  <Link
                    className={buttonClass('primary')}
                    href={
                      items(data)[0]
                        ? pathFor(role, `/courses/${items(data)[0].id}`)
                        : pathFor(role, '/courses')
                    }
                  >
                    {role === 'teacher' ? '进入课程工作台' : '继续学习'}
                  </Link>
                </div>
                <div className="edu-hero-icon">
                  <BookOpenText size={48} />
                </div>
              </Panel>
            )}
            <div className="edu-inline justify-between">
              <h2>{onlyCourses ? '课程列表' : '正在进行的课程'}</h2>
              <span className="edu-small edu-muted">
                {items(data).length} 门可访问课程
              </span>
            </div>
            {items(data).length ? (
              <div className="edu-grid">
                {items(data).map((c: D) => (
                  <Panel key={c.id} className="edu-course">
                    <div className="edu-course-top">
                      <div className="edu-course-icon">
                        <Books size={24} />
                      </div>
                      <Tag value={c.status || 'ready'} />
                    </div>
                    <h3>{c.name || c.title}</h3>
                    <p>{c.description || '查看课程资料、练习与学习反馈。'}</p>
                    <div className="edu-inline justify-between">
                      <span className="edu-small edu-muted">
                        {c.class_names?.join('、') || '课程空间'}
                      </span>
                      <Link
                        className={buttonClass('secondary', 'sm')}
                        href={pathFor(role, `/courses/${c.id}`)}
                      >
                        进入课程
                      </Link>
                    </div>
                  </Panel>
                ))}
              </div>
            ) : (
              <Blank
                title="还没有可访问的课程"
                description={
                  role === 'teacher'
                    ? '本版课程由初始化工具准备，并由管理员授予课程范围。'
                    : '加入班级后，课程会显示在这里。'
                }
              />
            )}
            <Notice>
              本地演示使用独立演示数据。模型研究能力尚未接通；课程流程和评分记录可以实际操作。
            </Notice>
          </>
        )}
      </AsyncContent>
    </Page>
  )
}

function Course({
  role,
  id,
  tab,
  session,
  notify,
}: {
  role: string
  id: string
  tab: string
  session: D
  notify: (s: string) => void
}) {
  const state = useRemote('/courses/' + id)
  const tabs =
    role === 'teacher'
      ? [
          ['overview', '课程概览'],
          ['resources', '内容资料'],
          ['generate', '练习准备'],
          ['assignments', '作业与批阅'],
          ['learners', '学习状态'],
          ['settings', '课程设置'],
        ]
      : [
          ['overview', '课程概览'],
          ['resources', '学习资料'],
          ['assignments', '课程作业'],
          ['state', '学习状态与建议'],
        ]
  return (
    <AsyncContent state={state}>
      {(d) => (
        <Page
          title={d.course?.name || '课程'}
          subtitle={d.description || '课程内容、学习任务与反馈集中管理。'}
          actions={
            <Button onClick={state.reload} icon={<ArrowClockwise />}>
              刷新
            </Button>
          }
        >
          <nav className="edu-tabs" aria-label="课程页面">
            {tabs.map(([t, n]) => (
              <Link
                key={t}
                href={pathFor(role, `/courses/${id}/${t}`)}
                aria-current={tab === t ? 'page' : undefined}
                className={tab === t ? 'active' : ''}
              >
                {n}
              </Link>
            ))}
          </nav>
          {tab === 'generate' && <Generator course={d} notify={notify} />}
          {tab === 'state' && (
            <LearningState
              course={d}
              studentId={session.principal.user_id}
              role={role}
            />
          )}
          {tab === 'learners' && <Learners course={d} role={role} />}
          {tab === 'settings' && (
            <CourseSettings course={d} reload={state.reload} notify={notify} />
          )}
          {tab === 'overview' && (
            <>
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
                <StatCard
                  icon={<Files size={22} />}
                  iconClass="bg-neutral-100 text-neutral-600"
                  label="课程内容"
                  value={d.resources?.length || 0}
                />
                <StatCard
                  icon={<ClipboardText size={22} />}
                  iconClass="bg-neutral-100 text-neutral-600"
                  label="已布置作业"
                  value={d.bindings?.length || 0}
                />
                <StatCard
                  icon={<UsersThree size={22} />}
                  iconClass="bg-neutral-100 text-neutral-600"
                  label="关联班级"
                  value={d.classes?.length || 0}
                />
              </div>
              <Panel padded>
                <h2 className="mb-3">学习目标</h2>
                <p className="edu-content">
                  {d.learnings || '根据课程资料与练习逐步巩固知识点。'}
                </p>
                <div className="edu-inline mt-4">
                  {d.knowledge_points?.map((k: D) => (
                    <StatusPill key={k.id} status="neutral">
                      {k.label}
                    </StatusPill>
                  ))}
                </div>
              </Panel>
            </>
          )}
          {['overview', 'resources'].includes(tab) && (
            <>
              <div className="edu-inline justify-between">
                <h2>{role === 'teacher' ? '课程内容' : '学习资料与练习'}</h2>
                {role === 'teacher' && (
                  <Link
                    className={buttonClass('secondary', 'sm')}
                    href={pathFor(role, `/courses/${id}/generate`)}
                  >
                    准备练习
                  </Link>
                )}
              </div>
              {d.resources?.length ? (
                <Panel divided>
                  {d.resources.map((r: D) => (
                    <div className="edu-list-row" key={r.id}>
                      <div className="edu-inline">
                        <BookOpenText size={23} />
                        <div>
                          <h3>{r.title || '课程资源'}</h3>
                          <p>
                            {label(r.kind)} · 第 {r.version} 版{' '}
                            {r.forced_review ? '· 需要教师复核' : ''}
                          </p>
                        </div>
                      </div>
                      <div className="actions">
                        <Tag
                          value={
                            r.publication_action === 'publish'
                              ? 'published'
                              : r.processing_state
                          }
                        />
                        <Link
                          className={buttonClass('secondary', 'sm')}
                          href={pathFor(role, `/resources/${r.resource_id}`)}
                        >
                          {role === 'teacher' ? '查看与发布' : '开始阅读'}
                        </Link>
                      </div>
                    </div>
                  ))}
                </Panel>
              ) : (
                <Blank title="课程内容还在准备" />
              )}
              {role === 'teacher' && tab === 'resources' && (
                <SourceForm course={d} notify={notify} onDone={state.reload} />
              )}
              {role === 'student' && tab === 'resources' && (
                <Notes courseId={Number(id)} />
              )}
            </>
          )}
          {['overview', 'assignments'].includes(tab) && (
            <>
              <h2>课程作业</h2>
              {d.bindings?.length ? (
                <Panel divided>
                  {d.bindings.map((b: D) => (
                    <div key={b.id || b.binding_id} className="edu-list-row">
                      <div>
                        <h3>{b.title}</h3>
                        <p>
                          <Clock className="inline me-1" />
                          截止 {date(b.due_at)}
                        </p>
                      </div>
                      <div className="actions">
                        <Tag value={b.status} />
                        <Link
                          className={buttonClass('secondary', 'sm')}
                          href={pathFor(
                            role,
                            `/bindings/${b.id || b.binding_id}`
                          )}
                        >
                          {role === 'teacher' ? '查看提交' : '查看作业'}
                        </Link>
                      </div>
                    </div>
                  ))}
                </Panel>
              ) : (
                <Blank
                  title="还没有布置作业"
                  description={
                    role === 'teacher'
                      ? '先准备并发布练习，再在练习详情中布置给班级。'
                      : '教师布置后，作业会显示在这里。'
                  }
                />
              )}
            </>
          )}
        </Page>
      )}
    </AsyncContent>
  )
}
function CourseSettings({
  course: d,
  reload,
  notify,
}: {
  course: D
  reload: () => void
  notify: (s: string) => void
}) {
  const [name, setName] = useState(d.course.name),
    [description, setDescription] = useState(d.description || ''),
    [learnings, setLearnings] = useState(d.learnings || ''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      await api('/courses/' + d.course.id, {
        method: 'PATCH',
        etag: d.etag,
        body: {
          name,
          description,
          learnings,
        },
      })
      notify('课程设置已保存')
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel padded>
      <form className="edu-form" onSubmit={save}>
        <h2>课程设置</h2>
        <Field label="课程名称">
          <input
            className={INPUT}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </Field>
        <Field label="课程简介">
          <textarea
            className={TEXTAREA}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <Field label="学习目标">
          <textarea
            className={TEXTAREA}
            value={learnings}
            onChange={(e) => setLearnings(e.target.value)}
          />
        </Field>
        {error && <Notice error>{error}</Notice>}
        <div>
          <Button type="submit" variant="primary" loading={busy}>
            保存设置
          </Button>
        </div>
      </form>
    </Panel>
  )
}
function SourceForm({
  course: d,
  notify,
  onDone,
}: {
  course: D
  notify: (s: string) => void
  onDone: () => void
}) {
  const [title, setTitle] = useState(''),
    [body, setBody] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      await api(`/courses/${d.course.id}/resources`, {
        method: 'POST',
        etag: d.etag,
        body: {
          kind: 'source_text',
          title,
          text: body,
          policy_version_id: d.policy_version_id,
          knowledge_point_ids: (d.knowledge_points || []).map((x: D) => x.id),
        },
      })
      setTitle('')
      setBody('')
      notify('资料已保存为新版本')
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel padded>
      <form className="edu-form" onSubmit={save}>
        <h2>添加文本资料</h2>
        <Field label="资料标题">
          <input
            className={INPUT}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </Field>
        <Field label="资料正文">
          <textarea
            className={TEXTAREA}
            rows={7}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            required
            placeholder="粘贴课程讲义或知识点说明"
          />
        </Field>
        {error && <Notice error>{error}</Notice>}
        <div>
          <Button type="submit" loading={busy} variant="primary">
            保存资料
          </Button>
        </div>
      </form>
    </Panel>
  )
}
function Generator({
  course: d,
  notify,
}: {
  course: D
  notify: (s: string) => void
}) {
  const router = useRouter(),
    sources = (d.resources || []).filter(
      (r: D) => r.kind === 'source_text' || r.kind === 'lesson'
    ),
    [source, setSource] = useState(sources[0]?.id || ''),
    [requirement, setRequirement] = useState(
      '练习 Java 布尔表达式、条件判断与短路求值。'
    ),
    [selected, setSelected] = useState<string[]>(
      (d.knowledge_points || []).map((x: D) => x.id)
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [generationMode, setGenerationMode] = useState('rule_template'),
    [targetStudent, setTargetStudent] = useState(''),
    [confirmed, setConfirmed] = useState(false),
    [budgetLimit, setBudgetLimit] = useState(''),
    [maxCalls, setMaxCalls] = useState(8)
  const fixtureMode = generationMode === 'fixture'
  const executeMode = generationMode === 'execute'
  const executeCapability = d.capabilities?.find((value: D) => value.key === 'generation_execute')
  const fixtureCapability = d.capabilities?.some((value: D) => value.key === 'generation_fixture')
  const eligibleTargets = (d.knowledge_points || []).filter(
    (point: D) => executeMode ? executeCapability?.concept_codes?.includes(point.code) : point.code === 'java.condition'
  )
  const students = Array.from(new Map<number, D>(
    (d.classes || []).flatMap((group: D) =>
      (group.members || []).map((member: D): [number, D] => [member.user_id, member])
    )
  ).values())
  async function generate(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const task = await api(`/courses/${d.course.id}/generation-runs`, {
        method: 'POST',
        etag: d.etag,
        body: {
          ...((fixtureMode || executeMode)
            ? { mode: 'research_generator', run_mode: generationMode, target_student_id: Number(targetStudent),
                max_calls: fixtureMode ? 4 : maxCalls, ...(executeMode ? { confirm_paid: confirmed } : {}) }
            : { mode: 'rule_template' }),
          source_version_id: source,
          policy_version_id: d.policy_version_id,
          knowledge_point_ids: selected,
          requirement,
          budget_limit: executeMode ? Number(budgetLimit) : 0,
          max_attempts: 1,
        },
      })
      notify(executeMode ? '已提交出题任务' : fixtureMode ? '合成候选已生成，请审核后发布' : '规则练习已生成，请检查后发布')
      if (task.result_refs?.resource_id)
        router.push(
          pathFor('teacher', `/resources/${task.result_refs.resource_id}`)
        )
      else router.push(pathFor('teacher', `/tasks/${task.id}`))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="edu-split">
      <Panel padded>
        <form className="edu-form" onSubmit={generate}>
          <h2>准备一份练习</h2>
          <p className="edu-muted">
            选择课程资料与目标知识点，生成待检查的练习。
          </p>
          {(fixtureCapability || executeCapability) && (
            <Field label="生成方式">
              <select className={INPUT} value={generationMode} onChange={(e) => {
                const mode = e.target.value
                setGenerationMode(mode)
                setConfirmed(false)
                setSelected((d.knowledge_points || []).filter((point: D) =>
                  mode === 'rule_template' || (mode === 'fixture' && point.code === 'java.condition') ||
                  (mode === 'execute' && executeCapability?.concept_codes?.includes(point.code))
                ).map((point: D) => point.id))
              }}>
                <option value="rule_template">规则模板</option>
                {fixtureCapability && <option value="fixture">研究生成联调（合成样例）</option>}
                {executeCapability && <option value="execute">模型生成 Java 练习</option>}
              </select>
            </Field>
          )}
          {(fixtureMode || executeMode) && (
            <Field label={fixtureMode ? '联调学生' : '目标学生'}>
              <select className={INPUT} value={targetStudent} onChange={(e) => setTargetStudent(e.target.value)} required>
                <option value="">请选择联调学生</option>
                {students.map((student) => (
                  <option key={student.user_id} value={student.user_id}>{student.display_name}</option>
                ))}
              </select>
            </Field>
          )}
          <Field label="来源资料">
            <select
              className={INPUT}
              value={source}
              onChange={(e) => setSource(e.target.value)}
              required
            >
              <option value="">请选择已准备好的资料</option>
              {sources.map((s: D) => (
                <option key={s.id} value={s.id}>
                  {s.title} · 第{s.version}版
                </option>
              ))}
            </select>
          </Field>
          <fieldset>
            <legend className="edu-small font-semibold mb-2">目标知识点</legend>
            <div className="edu-stack">
              {((fixtureMode || executeMode) ? eligibleTargets : d.knowledge_points)?.map((k: D) => (
                <label key={k.id} className="edu-inline">
                  <input
                    type="checkbox"
                    checked={selected.includes(k.id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, k.id]
                          : selected.filter((x) => x !== k.id)
                      )
                    }
                    className="!h-4 !w-4"
                  />
                  {k.label}
                </label>
              ))}
            </div>
          </fieldset>
          <Field label="练习要求">
            <textarea
              className={TEXTAREA}
              value={requirement}
              maxLength={4000}
              onChange={(e) => setRequirement(e.target.value)}
              required
            />
          </Field>
          {executeMode && (
            <>
              <Field label="课程审批额度">
                <input className={INPUT} type="number" min="0.000001" step="any"
                  max={executeCapability.maximum_task_budget} required value={budgetLimit}
                  onChange={(e) => { setBudgetLimit(e.target.value); setConfirmed(false) }} />
              </Field>
              <Field label="模型请求次数上限">
                <input className={INPUT} type="number" min={1} max={8} required value={maxCalls}
                  onChange={(e) => { setMaxCalls(Number(e.target.value)); setConfirmed(false) }} />
              </Field>
              <label className="edu-inline">
                <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} required />
                我确认本次出题会发送所选资料和练习要求给模型，供应商可能计费。
              </label>
            </>
          )}
          {error && <Notice error>{error}</Notice>}
          <div>
            <Button
              variant="primary"
              type="submit"
              loading={busy}
              disabled={!source || !selected.length || (fixtureMode && (!targetStudent || selected.length !== 1)) ||
                (executeMode && (!targetStudent || !confirmed || !budgetLimit))}
            >
              {executeMode ? '提交模型出题' : fixtureMode ? '生成合成候选' : '生成规则练习'}
            </Button>
          </div>
        </form>
      </Panel>
      <Panel padded className="edu-stack">
        <div className="edu-inline">
          <ListChecks size={25} />
          <h3>本次生成方式</h3>
        </div>
        <p>固定 Java 判断题模板</p>
        <p className="edu-small edu-muted">
          结果由规则产生，不调用语言模型。生成后仍需完成内容检查和班级发布。
        </p>
        <Notice>
          多智能体研究生成尚未接通。本版不会把规则题目标记为 AI 生成。
        </Notice>
      </Panel>
    </div>
  )
}

function Questions({
  content,
  answers,
  onChange,
  readOnly = false,
}: {
  content: D
  answers?: D[]
  onChange?: (id: string, value: any) => void
  readOnly?: boolean
}) {
  return (
    <div className="edu-stack">
      {content?.body_text && (
        <Panel padded>
          <p className="edu-content">{content.body_text}</p>
        </Panel>
      )}
      {content?.questions?.map((q: D, i: number) => {
        const value = answers?.find((a) => a.item_id === q.item_id)?.value
        return (
          <Panel className="edu-question" key={q.item_id}>
            <div className="edu-inline justify-between">
              <h3>题目 {i + 1}</h3>
              <span className="edu-small edu-muted">{q.maximum_score} 分</span>
            </div>
            <p className="edu-content my-4">{q.prompt}</p>
            {onChange && !readOnly ? (
              q.type === 'boolean' ? (
                <div className="edu-choices">
                  {[true, false].map((v) => (
                    <label className="edu-choice" key={String(v)}>
                      <input
                        type="radio"
                        name={q.item_id}
                        checked={value === v}
                        onChange={() => onChange(q.item_id, v)}
                      />
                      {v ? '正确（true）' : '错误（false）'}
                    </label>
                  ))}
                </div>
              ) : q.type === 'single_choice' ? (
                <div className="edu-choices">
                  {q.choices?.map((c: D) => (
                    <label className="edu-choice" key={c.id || c.value}>
                      <input
                        type="radio"
                        name={q.item_id}
                        checked={value === (c.id || c.value)}
                        onChange={() => onChange(q.item_id, c.id || c.value)}
                      />
                      {c.text || c.label}
                    </label>
                  ))}
                </div>
              ) : (
                <textarea
                  className={TEXTAREA + ' font-mono'}
                  rows={q.type === 'code_text' ? 9 : 4}
                  value={value ?? ''}
                  onChange={(e) => onChange(q.item_id, e.target.value)}
                  placeholder={
                    q.type === 'code_text'
                      ? '在此保存 Java 代码文本，本版不执行代码'
                      : '写下你的回答'
                  }
                />
              )
            ) : (
              answers && (
                <div className="edu-notice">
                  <span>
                    作答：
                    {value === true
                      ? 'true'
                      : value === false
                        ? 'false'
                        : String(value ?? '未作答')}
                  </span>
                </div>
              )
            )}
          </Panel>
        )
      })}
    </div>
  )
}
function Resource({
  role,
  id,
  notify,
}: {
  role: string
  id: string
  notify: (s: string) => void
}) {
  const state = useRemote('/resources/' + id),
    course = useRemote(state.data ? '/courses/' + state.data.course_id : null),
    [classId, setClassId] = useState(''),
    [reason, setReason] = useState('已核对题目、答案与课程目标。'),
    [busy, setBusy] = useState(''),
    [error, setError] = useState('')
  useEffect(() => {
    if (course.data?.classes?.[0] && !classId)
      setClassId(String(course.data.classes[0].id))
  }, [course.data])
  async function command(kind: string, body: D) {
    if (!state.data) return
    setBusy(kind)
    setError('')
    try {
      await api(`/resource-versions/${state.data.version.id}/${kind}`, {
        method: 'POST',
        etag: state.data.etag,
        body,
      })
      notify(kind === 'checks' ? '检查记录已保存' : '发布状态已更新')
      state.reload()
      course.reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  return (
    <AsyncContent state={state}>
      {(d) => {
        const validation =
            d.checks?.findLast(
              (c: D) => c.check_kind === 'deterministic' && c.verdict === 'pass'
            ) ||
            d.checks?.find(
              (c: D) => c.check_kind === 'deterministic' && c.verdict === 'pass'
            ),
          review = d.checks?.findLast(
            (c: D) => c.check_kind === 'human_review' && c.verdict === 'approve'
          ),
          events = (d.publication_events || [])
            .filter((p: D) => String(p.usergroup_id) === classId)
            .sort((a: D, b: D) => b.event_no - a.event_no),
          event = events[0]
        return (
          <Page
            title={d.title}
            subtitle={`课程内容 · 第 ${d.version.version} 版`}
            actions={
              <Link
                className={buttonClass('secondary')}
                href={pathFor(role, `/courses/${d.course_id}/resources`)}
              >
                返回课程
              </Link>
            }
          >
            <div className="edu-meta">
              <Tag
                value={
                  d.version.publication_action === 'publish'
                    ? 'published'
                    : d.version.processing_state
                }
              />
              <span>
                {d.version.generation_mode === 'rule_template'
                  ? '规则模板'
                  : d.version.generation_mode === 'research_generator'
                    ? '研究生成候选'
                    : '课程资料'}
              </span>
              <span>{label(d.version.provenance)}</span>
            </div>
            <div className="edu-split">
              <Questions content={d.content} />
              <div className="edu-stack">
                {role === 'teacher' ? (
                  <Panel padded className="edu-stack">
                    <h2>内容检查与发布</h2>
                    <div className="edu-inline">
                      <Tag value={d.version.validation_verdict || 'pending'} />
                      {d.version.forced_review && (
                        <Tag
                          value={d.version.review_verdict || 'needs_review'}
                        />
                      )}
                    </div>
                    <Button
                      loading={busy === 'checks'}
                      onClick={() =>
                        command('checks', {
                          check_kind: 'deterministic',
                          policy_version_id:
                            course.data?.policy_version_id ||
                            d.version.policy_version_id,
                        })
                      }
                      icon={<ListChecks />}
                    >
                      运行内容检查
                    </Button>
                    <Field label="处理说明">
                      <textarea
                        className={TEXTAREA}
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        maxLength={4000}
                      />
                    </Field>
                    {validation && (
                      <div className="edu-inline">
                        <Button
                          disabled={!!busy || !reason.trim()}
                          onClick={() =>
                            command('checks', {
                              check_kind: 'human_review',
                              policy_version_id:
                                course.data?.policy_version_id ||
                                d.version.policy_version_id,
                              validation_id: validation.id,
                              verdict: 'approve',
                              reason,
                            })
                          }
                        >
                          确认审核通过
                        </Button>
                      </div>
                    )}
                    <Field label="发布班级">
                      <select
                        className={INPUT}
                        value={classId}
                        onChange={(e) => setClassId(e.target.value)}
                      >
                        {course.data?.classes?.map((c: D) => (
                          <option key={c.id} value={c.id}>
                            {c.name}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Button
                      variant="primary"
                      loading={busy === 'publication-events'}
                      disabled={
                        !validation ||
                        !classId ||
                        !reason.trim() ||
                        (d.version.forced_review && !review)
                      }
                      onClick={() =>
                        command('publication-events', {
                          action:
                            event?.action === 'publish'
                              ? 'withdraw'
                              : 'publish',
                          usergroup_id: Number(classId),
                          expected_event_no: event?.event_no || 0,
                          previous_event_id: event?.id || null,
                          validation_id: validation?.id || null,
                          review_id: review?.id || null,
                          policy_version_id:
                            course.data?.policy_version_id ||
                            d.version.policy_version_id,
                          reason,
                        })
                      }
                    >
                      {event?.action === 'publish'
                        ? '撤回该班发布'
                        : '发布给班级'}
                    </Button>
                    {d.version.forced_review && !review && (
                      <p className="edu-small edu-muted">
                        此版本必须完成教师复核后才能发布。
                      </p>
                    )}
                    {error && <Notice error>{error}</Notice>}
                  </Panel>
                ) : (
                  <Panel padded className="edu-stack">
                    <h2>阅读与练习</h2>
                    <p className="edu-small edu-muted">
                      阅读完成后，回到课程作业开始你的练习。
                    </p>
                    <Link
                      className={buttonClass('primary')}
                      href={pathFor(
                        role,
                        `/courses/${d.course_id}/assignments`
                      )}
                    >
                      查看课程作业
                    </Link>
                    <Notice>
                      课程问答尚未接通，当前支持资料阅读与作业反馈。
                    </Notice>
                  </Panel>
                )}
                {role === 'teacher' && d.restricted_content && (
                  <Panel padded>
                    <h3>教师参考答案</h3>
                    {(d.version.generation_mode === 'research_generator' || d.candidate_history) && (
                      <>
                        <p>{d.version.provenance === 'simulated' ? '合成样例' : '模型生成'} · 第 {d.version.version} 版</p>
                        <pre className="edu-content whitespace-pre-wrap">{d.restricted_content.grading_notes}</pre>
                        {d.candidate_history?.map((version: D) => (
                          <details key={version.version}>
                            <summary>第 {version.version} 版候选</summary>
                            {version.content.questions.map((question: D) => (
                              <pre key={question.item_id} className="edu-content whitespace-pre-wrap">{question.prompt}</pre>
                            ))}
                            <pre className="edu-content whitespace-pre-wrap">{version.grading_notes}</pre>
                          </details>
                        ))}
                      </>
                    )}
                    <div className="edu-stack mt-3">
                      {(
                        d.restricted_content.answer_keys ||
                        d.restricted_content.answers ||
                        []
                      ).map((a: D, i: number) => (
                        <p key={i} className="edu-small">
                          {a.item_id}：{String(a.value ?? a.answer)}{' '}
                          {a.rationale || a.explanation}
                        </p>
                      ))}
                    </div>
                  </Panel>
                )}
              </div>
            </div>
            {role === 'teacher' &&
              d.kind === 'exercise' &&
              event?.action === 'publish' &&
              course.data && (
                <BindingForm
                  resource={d}
                  course={course.data}
                  publication={event}
                  notify={notify}
                />
              )}
            <Panel padded>
              <h3 className="mb-3">版本与处理记录</h3>
              <div className="edu-timeline">
                {d.checks?.length ? (
                  d.checks.map((c: D) => (
                    <div key={c.id}>
                      <div className="edu-inline">
                        <Tag value={c.verdict} />
                        <span className="edu-small">
                          {c.check_kind === 'human_review'
                            ? '教师审核'
                            : '规则检查'}
                        </span>
                      </div>
                      <p>{date(c.created_at)}</p>
                    </div>
                  ))
                ) : (
                  <p className="edu-muted">当前显示你有权阅读的内容版本。</p>
                )}
              </div>
            </Panel>
            {role === 'student' && <Notes courseId={d.course_id} />}
          </Page>
        )
      }}
    </AsyncContent>
  )
}
function BindingForm({
  resource: r,
  course: c,
  publication: p,
  notify,
}: {
  resource: D
  course: D
  publication: D
  notify: (s: string) => void
}) {
  const router = useRouter(),
    [title, setTitle] = useState(r.title),
    [due, setDue] = useState(
      new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 16)
    ),
    [attempts, setAttempts] = useState('2'),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      const questions = r.content.questions || [],
        criteria = questions.map((q: D) => ({
          criterion_id: q.item_id,
          item_id: q.item_id,
          description: '按题目答案与要求评分',
          maximum_score: q.maximum_score,
          knowledge_point_ids: q.knowledge_point_ids || [],
        }))
      const b = await api(`/courses/${c.course.id}/bindings`, {
        method: 'POST',
        etag: c.etag,
        body: {
          assignment_id: null,
          title,
          description: '请独立完成练习，提交后查看反馈。',
          resource_version_id: r.version.id,
          rubric_version_id: null,
          new_rubric: {
            criteria,
            maximum_score: questions.reduce(
              (s: number, q: D) => s + q.maximum_score,
              0
            ),
          },
          policy_version_id: c.policy_version_id,
          publication_event_id: p.id,
          usergroup_id: p.usergroup_id,
          opens_at: new Date().toISOString(),
          due_at: new Date(due).toISOString(),
          max_attempts: Number(attempts),
          answer_release: 'after_effective',
          allow_review: true,
        },
      })
      notify('作业已布置给班级')
      router.push(pathFor('teacher', `/bindings/${b.id || b.binding_id}`))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel padded>
      <form className="edu-form" onSubmit={submit}>
        <h2>将此版本布置为作业</h2>
        <p className="edu-small edu-muted">
          题目和评分规则将在布置时冻结。班级：
          {c.classes?.find((x: D) => x.id === p.usergroup_id)?.name ||
            p.usergroup_id}
        </p>
        <Field label="作业标题">
          <input
            className={INPUT}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </Field>
        <div className="edu-form-row">
          <Field label="截止时间">
            <input
              type="datetime-local"
              className={INPUT}
              value={due}
              onChange={(e) => setDue(e.target.value)}
              required
            />
          </Field>
          <Field label="允许尝试次数">
            <input
              type="number"
              min={1}
              max={10}
              className={INPUT}
              value={attempts}
              onChange={(e) => setAttempts(e.target.value)}
              required
            />
          </Field>
        </div>
        {error && <Notice error>{error}</Notice>}
        <div>
          <Button type="submit" variant="primary" loading={busy}>
            布置作业
          </Button>
        </div>
      </form>
    </Panel>
  )
}
function Binding({
  role,
  id,
  notify,
  source,
}: {
  role: string
  id: string
  source?: string
  notify: (s: string) => void
}) {
  const router = useRouter(),
    state = useRemote(source || '/bindings/' + id),
    submissions = useRemote(
      role === 'teacher' ? `/bindings/${id}/submissions` : null
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  async function start(displayed: D) {
    setBusy(true)
    setError('')
    try {
      const d = source ? await api(source) : displayed
      const draft = d.recipient?.attempts?.find((a: D) => a.status === 'draft')
      if (draft) {
        router.push(pathFor('student', `/attempts/${draft.id}`))
        return
      }
      const a = await api(`/bindings/${id}/attempts`, {
        method: 'POST',
        etag: d.etag,
        body: {
          recipient_id: d.recipient.id,
          resubmission_grant_id: d.recipient.active_grants?.[0]?.id || null,
        },
      })
      router.push(pathFor('student', `/attempts/${a.id}`))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <AsyncContent state={state}>
      {(d) => (
        <Page
          title={d.title}
          subtitle={d.description}
          actions={
            <Link
              className={buttonClass('secondary')}
              href={pathFor(role, `/courses/${d.course_id}/assignments`)}
            >
              返回课程
            </Link>
          }
        >
          <div className="edu-meta">
            <Tag value={d.status} />
            <span>截止 {date(d.due_at)}</span>
            <span>最多 {d.max_attempts} 次尝试</span>
            <span>
              满分{' '}
              {d.maximum_score ||
                d.grading_criteria?.reduce(
                  (s: number, x: D) => s + x.maximum_score,
                  0
                )}{' '}
              分
            </span>
          </div>
          {error && <Notice error>{error}</Notice>}
          {role === 'student' ? (
            <>
              <Panel padded className="edu-stack">
                <h2>完成这次练习</h2>
                <p>每次提交都会保留记录。请先保存草稿，再确认提交。</p>
                <div>
                  <Button
                    variant="primary"
                    loading={busy}
                    disabled={d.status !== 'assigned' || !d.recipient}
                    onClick={() => start(d)}
                  >
                    {d.recipient?.attempts?.some((a: D) => a.status === 'draft')
                      ? '继续作答'
                      : '开始新的尝试'}
                  </Button>
                </div>
              </Panel>
              {d.own_submission_ids?.length > 0 && (
                <Panel padded>
                  <h2 className="mb-2">我的提交记录</h2>
                  {d.own_submission_ids.map((s: string, i: number) => (
                    <div className="edu-list-row" key={s}>
                      <span>提交记录 {i + 1}</span>
                      <Link
                        href={pathFor(role, `/submissions/${s}`)}
                        className={buttonClass('secondary', 'sm')}
                      >
                        查看结果
                      </Link>
                    </div>
                  ))}
                </Panel>
              )}
              <Questions content={d.question_content || { questions: [] }} />
            </>
          ) : (
            <>
              <Panel padded>
                <h2>学生提交</h2>
                <p className="edu-small edu-muted">
                  仅显示当前授权班级中的正式提交，学生私人草稿不展示。
                </p>
              </Panel>
              <AsyncContent state={submissions}>
                {(s) =>
                  items(s).length ? (
                    <Panel divided>
                      {items(s).map((v: D) => (
                        <div
                          className="edu-list-row"
                          key={v.submission_id || v.receipt?.submission_id}
                        >
                          <div>
                            <h3>
                              {v.student_name ||
                                v.display_name ||
                                `学生 ${v.student_id || v.receipt?.student_id}`}
                            </h3>
                            <p>
                              提交于{' '}
                              {date(v.accepted_at || v.receipt?.accepted_at)}
                            </p>
                          </div>
                          <div className="actions">
                            <Tag
                              value={
                                v.evaluation_state ||
                                v.receipt?.evaluation_state ||
                                'submitted'
                              }
                            />
                            <Link
                              className={buttonClass('primary', 'sm')}
                              href={pathFor(
                                role,
                                `/submissions/${v.submission_id || v.receipt?.submission_id}`
                              )}
                            >
                              查看与批阅
                            </Link>
                          </div>
                        </div>
                      ))}
                    </Panel>
                  ) : (
                    <Blank
                      title="还没有学生提交"
                      description="学生提交后，可在此查看作答并给出反馈。"
                    />
                  )
                }
              </AsyncContent>
              <Panel padded>
                <h2 className="mb-2">布置范围</h2>
                {d.recipients?.map((r: D) => (
                  <div className="edu-list-row" key={r.id}>
                    <span>{r.display_name || `学生 ${r.student_id}`}</span>
                    <span className="edu-small edu-muted">
                      班级 {r.usergroup_id}
                    </span>
                  </div>
                ))}
              </Panel>
            </>
          )}
        </Page>
      )}
    </AsyncContent>
  )
}
function Attempt({ id, notify }: { id: string; notify: (s: string) => void }) {
  const router = useRouter(),
    state = useRemote('/attempts/' + id),
    [answers, setAnswers] = useState<D[]>([]),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(''),
    [error, setError] = useState(''),
    [confirm, setConfirm] = useState(false)
  useEffect(() => {
    if (state.data) {
      setAnswers(state.data.draft?.answers || [])
      setDirty(false)
    }
  }, [state.data])
  useEffect(() => {
    if (!dirty) return
    const f = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener('beforeunload', f)
    return () => window.removeEventListener('beforeunload', f)
  }, [dirty])
  async function save() {
    const d = state.data!
    setBusy('save')
    setError('')
    try {
      const a = await api(`/attempts/${id}/draft`, {
        method: 'PUT',
        etag: d.etag,
        body: { expected_version: d.draft_revision, draft: { answers } },
      })
      const updated = a.id ? a : await api('/attempts/' + id)
      state.setData(updated)
      setDirty(false)
      notify('草稿已保存')
      return updated
    } catch (e) {
      setError((e as Error).message)
      return null
    } finally {
      setBusy('')
    }
  }
  async function submit() {
    setBusy('submit')
    setError('')
    try {
      const d = state.data!
      const receipt = await api(`/attempts/${id}/submissions`, {
        method: 'POST',
        etag: d.etag,
        body: { expected_version: d.draft_revision, answer_hash: d.draft_hash },
      })
      notify('提交成功，回执已保存')
      router.replace(
        pathFor(
          'student',
          `/submissions/${receipt.submission_id || receipt.receipt?.submission_id}`
        )
      )
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  return (
    <AsyncContent state={state}>
      {(d) => (
        <Page
          title={`作答工作区 · 第 ${d.attempt_no} 次尝试`}
          subtitle="先保存草稿，再提交你的正式作答。"
          actions={
            <Link
              className={buttonClass('secondary')}
              href={pathFor('student', `/bindings/${d.binding_id}`)}
            >
              返回作业
            </Link>
          }
        >
          <div className="edu-inline">
            <Tag value={d.status} />
            <StatusPill status={dirty ? 'warning' : 'neutral'}>
              {dirty ? '有未保存内容' : '已保存'}
            </StatusPill>
          </div>
          {error && <Notice error>{error}</Notice>}
          <Questions
            content={d.question_content}
            answers={answers}
            readOnly={d.status !== 'draft'}
            onChange={(item_id, value) => {
              setAnswers((a) => [
                ...a.filter((x) => x.item_id !== item_id),
                { item_id, value },
              ])
              setDirty(true)
              setConfirm(false)
            }}
          />
          {d.status === 'draft' && (
            <Panel padded className="edu-stack">
              <div className="edu-inline">
                <Button
                  loading={busy === 'save'}
                  disabled={!!busy}
                  onClick={save}
                >
                  保存草稿
                </Button>
                <Button
                  variant="primary"
                  disabled={dirty || !!busy || !answers.length}
                  onClick={() => setConfirm(true)}
                >
                  确认提交
                </Button>
                <span className="edu-small edu-muted">
                  {dirty
                    ? '请先保存最新作答。'
                    : '正式提交后，本次作答将不可修改。'}
                </span>
              </div>
              {confirm && (
                <div className="edu-stack">
                  <Notice>
                    确认将当前已保存的作答正式提交？提交后可以查看结果，也可以按作业规则开启新的尝试。
                  </Notice>
                  <div className="edu-inline">
                    <Button
                      variant="primary"
                      loading={busy === 'submit'}
                      onClick={submit}
                    >
                      正式提交作答
                    </Button>
                    <Button disabled={!!busy} onClick={() => setConfirm(false)}>
                      继续检查
                    </Button>
                  </div>
                </div>
              )}
            </Panel>
          )}
        </Page>
      )}
    </AsyncContent>
  )
}
function Submission({
  role,
  id,
  notify,
}: {
  role: string
  id: string
  notify: (s: string) => void
}) {
  const state = useRemote('/submissions/' + id),
    [itemScores, setItemScores] = useState<Record<string, string>>({}),
    [feedback, setFeedback] = useState(''),
    [reason, setReason] = useState(''),
    [reviewText, setReviewText] = useState(''),
    [busy, setBusy] = useState(''),
    [error, setError] = useState('')
  async function grade(d: D) {
    setBusy('grade')
    setError('')
    try {
      const maximum =
          d.question_content?.questions?.reduce(
            (s: number, q: D) => s + q.maximum_score,
            0
          ) ||
          d.effective_grade?.maximum_score ||
          100,
        ev = await api(`/submissions/${id}/evaluations`, {
          method: 'POST',
          etag: d.etag,
          body: {
            source: 'teacher',
            score: (d.question_content?.questions || []).reduce(
              (sum: number, question: D) =>
                sum + Number(itemScores[question.item_id] || 0),
              0
            ),
            maximum_score: maximum,
            item_scores: (d.question_content?.questions || []).map(
              (question: D) => ({
                item_id: question.item_id,
                score: Number(itemScores[question.item_id] || 0),
                feedback,
              })
            ),
            feedback,
            reason,
            based_on_ai_evaluation_id: null,
          },
        }),
        fresh = await api('/submissions/' + id)
      const openReview = fresh.reviews?.find(
        (review: D) => review.status === 'open'
      )
      if (openReview) {
        await api(`/reviews/${openReview.id}/resolution`, {
          method: 'POST',
          etag: openReview.etag,
          body: {
            decision: 'revise',
            reason,
            evaluation_id: ev.id,
            expected_grade_revision: fresh.effective_grade.revision,
          },
        })
        notify('成绩已修订，复核已完成')
      } else {
        await api(`/submissions/${id}/grade-revisions`, {
          method: 'POST',
          etag: fresh.etag,
          body: {
            evaluation_id: ev.id,
            expected_version: fresh.effective_grade?.revision || 0,
            supersedes_id: fresh.effective_grade?.id || null,
            reason,
          },
        })
        notify('教师评分已生效，原记录保留')
      }
      state.reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  async function review(d: D) {
    setBusy('review')
    setError('')
    try {
      await api(`/submissions/${id}/reviews`, {
        method: 'POST',
        etag: d.etag,
        body: {
          grade_revision_id: d.effective_grade.id,
          request_text: reviewText,
        },
      })
      setReviewText('')
      notify('复核申请已提交')
      state.reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  async function resolve(d: D, r: D) {
    setBusy(r.id)
    try {
      await api(`/reviews/${r.id}/resolution`, {
        method: 'POST',
        etag: r.etag,
        body: {
          decision: 'retain',
          reason: reason || '核对评分与题目要求后，维持本次结果。',
          evaluation_id: null,
          expected_grade_revision: d.effective_grade.revision,
        },
      })
      notify('复核已处理')
      state.reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  return (
    <AsyncContent state={state}>
      {(d) => (
        <Page
          title={role === 'teacher' ? '作答批阅与反馈' : '作答结果与反馈'}
          subtitle={`提交于 ${date(d.receipt.accepted_at)} · 回执已保存`}
          actions={
            <Link
              className={buttonClass('secondary')}
              href={pathFor(role, `/bindings/${d.binding_id}`)}
            >
              返回作业
            </Link>
          }
        >
          {error && <Notice error>{error}</Notice>}
          <div className="edu-split">
            <div className="edu-stack">
              <Panel padded>
                <div className="edu-inline justify-between">
                  <div>
                    <p className="edu-small edu-muted">当前生效成绩</p>
                    <div className="edu-result-number my-3">
                      {d.effective_grade ? d.effective_grade.score : '待批阅'}
                      {d.effective_grade && (
                        <span className="text-xl edu-muted ms-2">
                          / {d.effective_grade.maximum_score}
                        </span>
                      )}
                    </div>
                  </div>
                  <Tag value={d.pipeline.evaluation} />
                </div>
                {d.effective_grade && (
                  <p className="edu-muted">
                    {label(d.effective_grade.judgment_source)} · 第{' '}
                    {d.effective_grade.revision} 次生效结果
                  </p>
                )}
                {d.effective_grade?.feedback?.text && (
                  <p className="edu-content mt-4">
                    {d.effective_grade.feedback.text}
                  </p>
                )}
              </Panel>
              <Questions
                content={d.question_content || { questions: [] }}
                answers={d.answer_snapshot.answers}
                readOnly
              />
              {role === 'student' && d.effective_grade && (
                <Panel padded>
                  <form
                    className="edu-form"
                    onSubmit={(e) => {
                      e.preventDefault()
                      review(d)
                    }}
                  >
                    <h2>对结果有疑问？</h2>
                    <Field label="复核说明">
                      <textarea
                        className={TEXTAREA}
                        value={reviewText}
                        onChange={(e) => setReviewText(e.target.value)}
                        required
                        maxLength={4000}
                        placeholder="说明你希望教师重新查看的题目和原因"
                      />
                    </Field>
                    <div>
                      <Button
                        type="submit"
                        loading={busy === 'review'}
                        disabled={d.reviews.some((r: D) => r.status === 'open')}
                      >
                        申请复核
                      </Button>
                    </div>
                  </form>
                </Panel>
              )}
              {d.reviews.length > 0 && (
                <Panel padded>
                  <h2 className="mb-4">复核记录</h2>
                  {d.reviews.map((r: D) => (
                    <div key={r.id} className="edu-list-row">
                      <div>
                        <Tag value={r.status} />
                        <p className="!text-base">{r.request_text}</p>
                        {r.resolution && <p>处理说明：{r.resolution}</p>}
                      </div>
                      {role === 'teacher' && r.status === 'open' && (
                        <Button
                          loading={busy === r.id}
                          onClick={() => resolve(d, r)}
                        >
                          核对后维持结果
                        </Button>
                      )}
                    </div>
                  ))}
                </Panel>
              )}
            </div>
            <div className="edu-stack">
              {role === 'teacher' && (
                <Panel padded>
                  <form
                    className="edu-form"
                    onSubmit={(e) => {
                      e.preventDefault()
                      grade(d)
                    }}
                  >
                    <h2>教师评分</h2>
                    {(d.question_content?.questions || []).map(
                      (question: D, index: number) => (
                        <Field
                          key={question.item_id}
                          label={`第 ${index + 1} 题得分（满分 ${question.maximum_score}）`}
                        >
                          <input
                            type="number"
                            className={INPUT}
                            value={itemScores[question.item_id] ?? ''}
                            onChange={(e) =>
                              setItemScores((current) => ({
                                ...current,
                                [question.item_id]: e.target.value,
                              }))
                            }
                            min={0}
                            max={question.maximum_score}
                            step="0.5"
                            required
                          />
                        </Field>
                      )
                    )}
                    <p className="edu-small">
                      合计：
                      {(d.question_content?.questions || []).reduce(
                        (sum: number, question: D) =>
                          sum + Number(itemScores[question.item_id] || 0),
                        0
                      )}{' '}
                      分
                    </p>
                    <Field label="给学生的反馈">
                      <textarea
                        className={TEXTAREA}
                        value={feedback}
                        onChange={(e) => setFeedback(e.target.value)}
                        required
                      />
                    </Field>
                    <Field label="评分或改分理由">
                      <textarea
                        className={TEXTAREA}
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        required
                      />
                    </Field>
                    <Button
                      type="submit"
                      variant="primary"
                      loading={busy === 'grade'}
                    >
                      {d.reviews.some((r: D) => r.status === 'open')
                        ? '修订成绩并完成复核'
                        : '确认评分并生效'}
                    </Button>
                    <p className="edu-small edu-muted">
                      这将新增一条教师评分，保留此前的评分历史。
                    </p>
                  </form>
                </Panel>
              )}
              <Panel padded className="edu-stack">
                <h3>后续学习状态</h3>
                <div className="edu-inline">
                  <span>课程统计</span>
                  <Tag value={d.pipeline.profile} />
                </div>
                <Notice>
                  本版展示课程规则统计；动态学习画像与个性化推荐研究能力尚未接通。
                </Notice>
                <Link
                  className={buttonClass('secondary')}
                  href={pathFor(
                    role,
                    `/courses/${d.course_id}/${role === 'teacher' ? 'learners' : 'state'}`
                  )}
                >
                  查看学习状态
                </Link>
              </Panel>
              <Panel padded>
                <h3 className="mb-4">评分历史</h3>
                <div className="edu-timeline">
                  {d.grade_history.map((g: D) => (
                    <div key={g.id}>
                      <strong className="edu-small">
                        第 {g.revision} 版 · {g.score} / {g.maximum_score}
                      </strong>
                      <p>
                        {label(g.judgment_source)} · {date(g.effective_at)}
                      </p>
                      <p>{g.reason}</p>
                    </div>
                  ))}
                </div>
              </Panel>
            </div>
          </div>
        </Page>
      )}
    </AsyncContent>
  )
}

function ResearchSuggestion({ decision, profile }: { decision: D; profile: D }) {
  if (decision.state === 'stale')
    return <Notice>画像依据已失效，请等待画像和推荐更新。</Notice>
  const cited = new Set(Object.values(decision.evidence_refs || {}).flat())
  const evidence = (profile.evidence || []).filter((e: D) => cited.has(e.id) && e.content_access === 'allowed')
  const prefix = `/courses/${decision.course_id}/students/${decision.student_id}/recommendations/${decision.id}/exercises/`
  return (
    <div className="edu-stack">
      <p className="edu-small edu-muted">
        正式 B 推荐 · 画像版本 {profile.version} · {label(decision.provenance)}
      </p>
      <p>{decision.reason}</p>
      {decision.concept_ids?.length > 0 && <p>目标知识点：{decision.concept_ids.join('、')}</p>}
      {evidence.length > 0 ? (
        <div>
          <h3>本次推荐引用的依据</h3>
          <ul>{evidence.map((e: D) => (
            <li key={e.id}>{e.source_type === 'effective_grade' ? e.summary_text :
              (e.summary?.text || (e.source_type === 'consented_note' ? '已授权的学习笔记' : '学习观察'))}
              {' · '}{date(e.observed_at)}</li>
          ))}</ul>
        </div>
      ) : <p className="edu-muted">暂无可展示的引用依据。</p>}
      {decision.state === 'insufficient_evidence' && <Notice>证据不足，本次未选择练习目标。</Notice>}
      {decision.state === 'no_content' && <Notice>已选定知识目标，暂时没有可访问的练习。</Notice>}
      {decision.state === 'ready' && (decision.items || []).filter((item: D) =>
        item.open_api_path === prefix + item.binding_id
      ).map((item: D) => (
        <div key={item.binding_id} className="edu-list-row">
          <span>{item.title}</span>
          <Link className={buttonClass('secondary', 'sm')} href={pathFor('student',
            `/recommended/${decision.course_id}/${decision.student_id}/${decision.id}/${item.binding_id}`)}>
            打开推荐练习
          </Link>
        </div>
      ))}
    </div>
  )
}

function LearningState({
  course,
  studentId,
  role,
}: {
  course: D
  studentId: number
  role: string
}) {
  const state = useRemote(
      `/courses/${course.course.id}/students/${studentId}/learning-state`
    ),
    rec = useRemote(
      `/courses/${course.course.id}/students/${studentId}/recommendations`
    ),
    [evidence, setEvidence] = useState<D | null>(null),
    [error, setError] = useState(''),
    [support, setSupport] = useState(''),
    [busy, setBusy] = useState(false),
    [saved, setSaved] = useState(false)
  async function seeEvidence(id: string) {
    setError('')
    try {
      setEvidence(await api('/evidence/' + id + '/content'))
    } catch (e) {
      setError((e as Error).message)
    }
  }
  async function intervene(d: D) {
    setBusy(true)
    try {
      await api(
        `/courses/${course.course.id}/students/${studentId}/interventions`,
        {
          method: 'POST',
          etag: d.etag,
          body: {
            profile_version_id: d.profile_version_id,
            recommendation_version_id: rec.data?.id || null,
            target_binding_id: null,
            action: 'record_support',
            reason: support,
            details: { support_note: support },
          },
        }
      )
      setSaved(true)
      setSupport('')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <AsyncContent state={state}>
      {(d) => (
        <div className="edu-stack">
          <Notice>
            当前显示课程记录统计，不代表 AI
            研究画像。没有证据的知识点保持“证据不足”。
          </Notice>
          {error && <Notice error>{error}</Notice>}
          <Panel padded>
            <div className="edu-inline justify-between mb-4">
              <h2>知识点记录</h2>
              <span className="edu-small edu-muted">状态版本 {d.version}</span>
            </div>
            {Object.keys(d.states || {}).length ? (
              Object.entries(d.states).map(([key, value]: [string, any]) => (
                <div className="edu-list-row" key={key}>
                  <div>
                    <h3>
                      {course.knowledge_points?.find(
                        (k: D) => k.id === key || k.code === key
                      )?.label ||
                        value.label ||
                        key}
                    </h3>
                    <p>
                      {value.reason ||
                        value.summary ||
                        '根据已生效评分形成的课程记录。'}
                    </p>
                  </div>
                  <Tag value={value.state || value.value || 'unknown'} />
                </div>
              ))
            ) : (
              <Blank
                title="还没有足够的学习记录"
                description="完成练习并形成生效评分后，在这里查看课程统计。"
              />
            )}
          </Panel>
          <Panel padded>
            <h2 className="mb-4">下一步学习建议</h2>
            {rec.loading || state.loading ? (
              <Notice>正在读取学习建议…</Notice>
            ) : (
              <AsyncContent state={rec}>
                {(decision) => {
                  if (
                    String(decision.course_id) !== String(course.course.id) ||
                    String(d.course_id) !== String(course.course.id) ||
                    decision.student_id !== studentId ||
                    d.student_id !== studentId ||
                    decision.profile_version_id !== d.profile_version_id
                  )
                    return (
                      <Notice>
                        学习记录已更新，请重新读取建议。{' '}
                        <Button
                          onClick={() => {
                            state.reload()
                            rec.reload()
                          }}
                        >
                          重新读取
                        </Button>
                      </Notice>
                    )
                  if (decision.strategy === 'none')
                    return <Notice>{decision.reason || '暂无学习建议。'}</Notice>
                  if (decision.strategy === 'research_B')
                    return <ResearchSuggestion decision={decision} profile={d} />
                  if (decision.strategy !== 'course_rule')
                    return <Notice>当前页面尚未接入此推荐来源。</Notice>
                  return (
                    <>
                      <p className="edu-small edu-muted mb-3">
                        来源：课程规则 · 画像版本 {d.version}
                      </p>
                      {decision.items?.length ? decision.items.map((item: D) => (
                        <div key={item.id} className="edu-list-row">
                          <div>
                            <h3>{item.title || '课程练习'}</h3>
                            <p>{item.reason || decision.reason}</p>
                          </div>
                          {item.binding_id && (
                            <Link className={buttonClass('secondary', 'sm')}
                              href={pathFor(role, `/bindings/${item.binding_id}`)}>
                              查看作业
                            </Link>
                          )}
                        </div>
                      )) : (
                        <Notice>当前没有可打开的课程建议。</Notice>
                      )}
                    </>
                  )
                }}
              </AsyncContent>
            )}
          </Panel>
          <Panel padded>
            <h2 className="mb-3">依据与来源</h2>
            {d.evidence?.length ? (
              d.evidence.map((e: D) => (
                <div className="edu-list-row" key={e.id}>
                  <div>
                    <p className="!text-base">
                      {e.summary_text ||
                        e.summary?.text ||
                        e.summary?.description ||
                        '来自已生效成绩的课程记录'}
                    </p>
                    <p>
                      {label(e.judgment_source)} · {label(e.provenance)} ·{' '}
                      {date(e.observed_at)}
                    </p>
                  </div>
                  {e.content_access === 'allowed' && (
                    <Button onClick={() => seeEvidence(e.id)}>查看依据</Button>
                  )}
                </div>
              ))
            ) : (
              <p className="edu-muted">暂无可追溯证据。</p>
            )}
            {evidence && (
              <div className="mt-4">
                {evidence.question_content ? (
                  <div className="edu-stack">
                    <Questions
                      content={evidence.question_content}
                      answers={evidence.answer_snapshot?.answers}
                      readOnly
                    />
                    {evidence.submission_id && (
                      <Link
                        className={buttonClass('secondary')}
                        href={pathFor(
                          role,
                          `/submissions/${evidence.submission_id}`
                        )}
                      >
                        查看来源作答与评分历史
                      </Link>
                    )}
                  </div>
                ) : (
                  <Notice>
                    {evidence.text ||
                      evidence.content_text ||
                      evidence.summary ||
                      '已读取授权来源，请以相关作业结果中的记录为准。'}
                  </Notice>
                )}
                <Button className="mt-2" onClick={() => setEvidence(null)}>
                  收起依据
                </Button>
              </div>
            )}
          </Panel>
          {role === 'teacher' && (
            <Panel padded>
              <form
                className="edu-form"
                onSubmit={(e) => {
                  e.preventDefault()
                  intervene(d)
                }}
              >
                <h2>记录教学支持</h2>
                <Field label="后续支持说明">
                  <textarea
                    className={TEXTAREA}
                    value={support}
                    onChange={(e) => setSupport(e.target.value)}
                    required
                  />
                </Field>
                <div>
                  <Button
                    type="submit"
                    loading={busy}
                    disabled={!d.profile_version_id}
                  >
                    保存支持记录
                  </Button>
                </div>
                {!d.profile_version_id && (
                  <p className="edu-small edu-muted">
                    形成首个课程状态版本后，可以记录基于该版本的教学支持。
                  </p>
                )}
                {saved && <Notice>支持记录已保存。</Notice>}
              </form>
            </Panel>
          )}
        </div>
      )}
    </AsyncContent>
  )
}
function Learners({ course, role }: { course: D; role: string }) {
  const members = Array.from(
      new Map<number, D>(
        (course.classes || []).flatMap((c: D) =>
          (c.members || []).map((s: D) => [
            s.user_id,
            { ...s, class_name: c.name },
          ])
        )
      ).values()
    ),
    [selected, setSelected] = useState(members[0]?.user_id || 0)
  return (
    <div className="edu-stack">
      <Panel padded>
        <Field label="选择授权班级内的学生">
          <select
            className={INPUT}
            value={selected}
            onChange={(e) => setSelected(Number(e.target.value))}
          >
            {members.map((s) => (
              <option key={s.user_id} value={s.user_id}>
                {s.display_name} · {s.class_name}
              </option>
            ))}
          </select>
        </Field>
      </Panel>
      {selected ? (
        <LearningState
          key={selected}
          course={course}
          studentId={selected}
          role={role}
        />
      ) : (
        <Blank title="当前课程没有可查看的学生" />
      )}
    </div>
  )
}
function Notes({ courseId }: { courseId: number }) {
  const state = useRemote(`/courses/${courseId}/notes`),
    [content, setContent] = useState(''),
    [activity, setActivity] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  useEffect(() => {
    if (state.data?.activities?.[0] && !activity)
      setActivity(String(state.data.activities[0].id))
  }, [state.data])
  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api(`/courses/${courseId}/notes`, {
        method: 'POST',
        etag: state.data?.etag,
        body: { activity_id: Number(activity), content_text: content },
      })
      setContent('')
      state.reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel padded className="edu-stack">
      <div className="edu-inline">
        <LockSimple size={22} />
        <h2>我的私人笔记</h2>
      </div>
      <p className="edu-small edu-muted">
        笔记默认仅本人可读，不会自动分享给教师或加入研究画像。
      </p>
      {state.error ? (
        <Notice error>{state.error}</Notice>
      ) : (
        <>
          <form className="edu-form" onSubmit={save}>
            <Field label="关联课程活动">
              <select
                className={INPUT}
                value={activity}
                onChange={(e) => setActivity(e.target.value)}
                required
              >
                {state.data?.activities?.map((a: D) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="记下理解与疑问">
              <textarea
                className={TEXTAREA}
                value={content}
                onChange={(e) => setContent(e.target.value)}
                required
              />
            </Field>
            {error && <Notice error>{error}</Notice>}
            <div>
              <Button type="submit" loading={busy} disabled={!activity}>
                保存私人笔记
              </Button>
            </div>
          </form>
          {state.data?.items?.map((n: D) => (
            <div key={n.id} className="edu-list-row">
              <div>
                <p className="edu-content !text-base">{n.content_text}</p>
                <p>{date(n.updated_at)}</p>
              </div>
            </div>
          ))}
        </>
      )}
    </Panel>
  )
}
function Task({ id }: { id: string }) {
  const state = useRemote('/tasks/' + id),
    course = useRemote(
      state.data?.course_id ? '/courses/' + state.data.course_id : null
    )
  return (
    <AsyncContent state={state}>
      {(d) => (
        <Page title="任务详情" subtitle="以服务端实际状态为准。">
          <Panel padded className="edu-stack">
            <div className="edu-inline">
              <Tag value={d.status} />
              <span>
                {d.operation === 'generate' ? '练习准备' : d.operation}
              </span>
            </div>
            <div className="edu-meta">
              <span>创建于 {date(d.created_at)}</span>
              <span>
                尝试 {d.attempt_count} / {d.max_attempts}
              </span>
            </div>
            {d.error_code && <Notice error>{d.error_code}</Notice>}
            {d.result_refs?.resource_id ? (
              <Link
                className={buttonClass('primary')}
                href={pathFor(
                  'teacher',
                  `/resources/${d.result_refs.resource_id}`
                )}
              >
                查看生成内容
              </Link>
            ) : (
              course.data?.resources
                ?.filter((r: D) =>
                  d.result_refs?.resource_version_ids?.includes(r.id)
                )
                .map((r: D) => (
                  <Link
                    key={r.id}
                    className={buttonClass('primary')}
                    href={pathFor('teacher', `/resources/${r.resource_id}`)}
                  >
                    查看生成内容
                  </Link>
                ))
            )}
            <Button onClick={state.reload}>刷新任务状态</Button>
            {d.status === 'running' && <Notice>模型正在出题。你可以稍后刷新查看结果。</Notice>}
          </Panel>
        </Page>
      )}
    </AsyncContent>
  )
}

function Admin({
  section,
  notify,
  session,
}: {
  section: string
  notify: (s: string) => void
  session: D
}) {
  const endpoint = ['accounts', 'access'].includes(section)
      ? '/admin/scopes'
      : section === 'policies'
        ? '/admin/policies'
        : section === 'audit'
          ? '/admin/audit'
          : '/admin/operations',
    state = useRemote(endpoint),
    [reason, setReason] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState('')
  const titles: Record<string, string> = {
    overview: '管理概览',
    accounts: '账号管理',
    access: '权限范围',
    policies: '教学策略',
    operations: '任务与能力',
    audit: '操作记录',
  }
  async function mutate(path: string, body: D, etag?: string) {
    setBusy(String(body.principal_id || body.grant_id || body.command))
    setError('')
    try {
      await api(path, { method: 'POST', body, etag: etag || state.data?.etag })
      notify('管理操作已保存')
      state.reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }
  return (
    <Page
      title={titles[section] || '系统管理'}
      subtitle="管理账号、权限与运行状态。教学正文按独立授权访问。"
      actions={
        <Button icon={<ArrowClockwise />} onClick={state.reload}>
          刷新
        </Button>
      }
    >
      {error && <Notice error>{error}</Notice>}
      <AsyncContent state={state}>
        {(d) => (
          <>
            {['overview', 'operations'].includes(section) ? (
              <>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  {[
                    ['accounts', '账号', UsersThree],
                    ['courses', '课程', Books],
                    ['running_tasks', '运行任务', Clock],
                    ['failed_tasks', '失败任务', WarningCircle],
                  ].map(([k, l, I]: any) => (
                    <StatCard
                      key={k}
                      icon={<I size={22} />}
                      iconClass="bg-neutral-100 text-neutral-600"
                      label={l}
                      value={d.counts?.[k] ?? 0}
                    />
                  ))}
                </div>
                <Panel padded>
                  <h2 className="mb-4">能力配置状态</h2>
                  {d.capabilities?.map((c: D, i: number) => (
                    <div className="edu-list-row" key={c.name || c.key || i}>
                      <div>
                        <h3>
                          {capabilityNames[c.name || c.key || c.capability] ||
                            c.label ||
                            c.name ||
                            c.key ||
                            c.capability ||
                            '研究能力'}
                        </h3>
                        <p>
                          {c.reason ||
                            (c.reason_code === 'DEMO_OFFLINE'
                              ? '本地演示尚未配置此能力，当前不会执行真实模型调用。'
                              : '尚未进行真实调用验收')}
                        </p>
                      </div>
                      <Tag value={c.status || 'unconfigured'} />
                    </div>
                  ))}
                </Panel>
                <Notice>
                  模型能力与教学效果尚未验收。规则生成、作答和教师批阅可以独立运行。任务预算单位：
                  {d.budget?.currency === 'unknown'
                    ? '费用口径未知'
                    : '配置额度'}
                  。
                </Notice>
                <h2>任务记录</h2>
                {d.tasks?.length ? (
                  <Panel divided>
                    {d.tasks.map((t: D) => (
                      <div key={t.id} className="edu-list-row">
                        <div>
                          <h3>
                            {operationNames[t.operation] || '教学处理任务'}
                          </h3>
                          <p>
                            {date(t.created_at)} · 尝试 {t.attempt_count} /{' '}
                            {t.max_attempts}
                          </p>
                        </div>
                        <div className="actions">
                          <Tag value={t.status} />
                          {t.can_cancel && (
                            <Button
                              disabled={!!busy}
                              onClick={() =>
                                mutate(
                                  `/tasks/${t.id}/commands`,
                                  {
                                    command: 'cancel',
                                    expected_execution_epoch: t.execution_epoch,
                                    reason: '管理员取消任务',
                                  },
                                  t.etag
                                )
                              }
                            >
                              取消任务
                            </Button>
                          )}
                        </div>
                      </div>
                    ))}
                  </Panel>
                ) : (
                  <Blank
                    title="暂无任务记录"
                    description="教师创建任务后会在这里显示运行元数据。"
                  />
                )}
              </>
            ) : section === 'accounts' ? (
              <>
                <Panel padded>
                  <Field label="管理操作理由">
                    <input
                      className={INPUT}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="停用、恢复或撤销会话前填写理由"
                    />
                  </Field>
                </Panel>
                <Panel divided>
                  {d.principals?.map((p: D) => (
                    <div key={p.id} className="edu-list-row">
                      <div>
                        <h3>{p.display_name || `账号 ${p.user_id}`}</h3>
                        <p>
                          {p.role_views
                            ?.map((r: string) => roles[r])
                            .join('、') || p.kind}
                        </p>
                      </div>
                      <div className="actions">
                        <Tag value={p.disabled_at ? 'paused' : 'ready'} />
                        <Button
                          disabled={
                            !reason.trim() ||
                            !!busy ||
                            p.id === session.principal.id
                          }
                          onClick={() =>
                            mutate('/admin/principals/commands', {
                              command: p.disabled_at
                                ? 'enable_v2'
                                : 'disable_v2',
                              principal_id: p.id,
                              reason,
                            })
                          }
                        >
                          {p.disabled_at ? '恢复账号' : '停用账号'}
                        </Button>
                        <Button
                          disabled={
                            !reason.trim() ||
                            !!busy ||
                            p.id === session.principal.id
                          }
                          onClick={() =>
                            mutate('/admin/principals/commands', {
                              command: 'revoke_sessions',
                              principal_id: p.id,
                              reason,
                            })
                          }
                        >
                          撤销会话
                        </Button>
                      </div>
                    </div>
                  ))}
                </Panel>
              </>
            ) : section === 'access' ? (
              <>
                <Panel padded>
                  <Field label="撤销授权理由">
                    <input
                      className={INPUT}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="说明范围调整原因"
                    />
                  </Field>
                </Panel>
                <Panel divided>
                  {d.grants?.map((g: D) => (
                    <div className="edu-list-row" key={g.id}>
                      <div>
                        <h3>
                          {d.principals?.find((p: D) => p.id === g.principal_id)
                            ?.display_name || '授权主体'}{' '}
                          · {actionName(g.scope?.action || g.action)}
                        </h3>
                        <p>
                          课程 {g.scope?.course_id ?? g.course_id ?? '组织范围'}
                          {g.scope?.usergroup_id
                            ? ` · 班级 ${g.scope.usergroup_id}`
                            : ''}
                          {g.revoked_at ? ' · 已撤销' : ''}
                        </p>
                      </div>
                      {!g.revoked_at && (
                        <Button
                          disabled={!reason.trim() || !!busy}
                          onClick={() =>
                            mutate('/admin/scopes/commands', {
                              command: 'revoke',
                              grant_id: g.id,
                              reason,
                            })
                          }
                        >
                          撤销授权
                        </Button>
                      )}
                    </div>
                  ))}
                </Panel>
                <GrantForm
                  scopes={d}
                  session={session}
                  notify={notify}
                  onDone={state.reload}
                />
              </>
            ) : section === 'policies' ? (
              <>
                {items(d).length ? (
                  items(d).map((p: D) => (
                    <PolicyCard
                      key={p.id}
                      policy={p}
                      listEtag={d.etag}
                      notify={notify}
                      onDone={state.reload}
                    />
                  ))
                ) : (
                  <Blank title="暂无策略版本" />
                )}
              </>
            ) : section === 'audit' ? (
              <Panel>
                <div className="edu-table-wrap">
                  <table className="edu-table">
                    <thead>
                      <tr>
                        <th>时间</th>
                        <th>操作</th>
                        <th>对象</th>
                        <th>操作主体</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items(d).map((e: D, i: number) => (
                        <tr key={e.id || i}>
                          <td>{date(e.created_at)}</td>
                          <td>{e.action || e.event_type}</td>
                          <td>
                            {e.target_type || e.object_type}{' '}
                            {String(e.target_id || e.object_id || '').slice(
                              0,
                              12
                            )}
                          </td>
                          <td>
                            {e.actor_name ||
                              String(e.actor_id || '').slice(0, 8)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!items(d).length && (
                  <p className="edu-empty-note">暂无可展示的操作记录。</p>
                )}
              </Panel>
            ) : null}
          </>
        )}
      </AsyncContent>
    </Page>
  )
}
const actionNames: Record<string, string> = {
  'content.read': '阅读课程内容',
  'content.generate': '准备练习',
  'content.edit': '编辑课程内容',
  'content.review': '审核内容',
  'content.publish': '发布内容',
  'task.manage': '布置与管理作业',
  'submission.grade': '批阅作答',
  'result.review': '处理复核',
  'profile.read': '读取学习状态',
  'intervention.write': '记录教学支持',
  'evidence.read': '读取授权依据',
  'export.read': '导出授权记录',
  'course.edit': '编辑课程设置',
  'course.create': '创建课程',
  'access.manage': '管理授权',
  'policy.manage': '管理策略',
  'operations.manage': '管理运行任务',
  'audit.metadata': '读取审计元数据',
}
function actionName(x: string) {
  return actionNames[x] || x
}
function GrantForm({
  scopes: d,
  session,
  notify,
  onDone,
}: {
  scopes: D
  session: D
  notify: (s: string) => void
  onDone: () => void
}) {
  const [principal, setPrincipal] = useState(''),
    [course, setCourse] = useState(''),
    [group, setGroup] = useState(''),
    [action, setAction] = useState('content.read'),
    [reason, setReason] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  const courseIds = Array.from(
    new Set<number>(
      (d.grants || [])
        .map((g: D) => g.scope?.course_id || g.course_id)
        .filter(Boolean)
    )
  )
  async function grant(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      await api('/admin/scopes/commands', {
        method: 'POST',
        etag: d.etag,
        body: {
          command: 'grant',
          principal_id: principal,
          course_id: Number(course),
          usergroup_id: group ? Number(group) : null,
          student_id: null,
          action,
          starts_at: new Date().toISOString(),
          expires_at: null,
          reason,
        },
      })
      notify('范围授权已保存')
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel padded>
      <form className="edu-form" onSubmit={grant}>
        <h2>授予课程范围</h2>
        <div className="edu-form-row">
          <Field label="授权账号">
            <select
              className={INPUT}
              value={principal}
              onChange={(e) => setPrincipal(e.target.value)}
              required
            >
              <option value="">请选择其他账号</option>
              {d.principals
                ?.filter(
                  (p: D) => p.id !== session.principal.id && p.kind === 'human'
                )
                .map((p: D) => (
                  <option key={p.id} value={p.id}>
                    {p.display_name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="授权动作">
            <select
              className={INPUT}
              value={action}
              onChange={(e) => setAction(e.target.value)}
            >
              {Object.entries(actionNames)
                .filter(
                  ([k]) =>
                    ![
                      'course.create',
                      'export.read',
                      'access.manage',
                      'policy.manage',
                      'operations.manage',
                      'audit.metadata',
                    ].includes(k)
                )
                .map(([k, n]) => (
                  <option key={k} value={k}>
                    {n}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="课程范围">
            <select
              className={INPUT}
              value={course}
              onChange={(e) => setCourse(e.target.value)}
              required
            >
              <option value="">请选择课程</option>
              {d.courses?.length
                ? d.courses.map((c: D) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))
                : courseIds.map((c) => (
                    <option key={c} value={c}>
                      课程 {c}
                    </option>
                  ))}
            </select>
          </Field>
          <Field label="班级范围">
            <select
              className={INPUT}
              value={group}
              onChange={(e) => setGroup(e.target.value)}
            >
              <option value="">课程内全部授权班级</option>
              {d.classes?.map((c: D) => (
                <option key={c.usergroup_id} value={c.usergroup_id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="授权理由">
          <input
            className={INPUT}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            required
          />
        </Field>
        {error && <Notice error>{error}</Notice>}
        <div>
          <Button type="submit" variant="primary" loading={busy}>
            保存授权
          </Button>
        </div>
      </form>
    </Panel>
  )
}
function PolicyCard({
  policy: p,
  listEtag,
  notify,
  onDone,
}: {
  policy: D
  listEtag: string
  notify: (s: string) => void
  onDone: () => void
}) {
  const [review, setReview] = useState(p.rules.require_human_review),
    [allow, setAllow] = useState(p.rules.allow_review),
    [reason, setReason] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    try {
      await api('/admin/policies', {
        method: 'POST',
        etag: listEtag,
        body: {
          policy_type: p.policy_type,
          course_id: p.course_id,
          family_id: p.family_id,
          expected_version: p.version,
          parent_policy_id: p.parent_policy_id,
          rules: {
            ...p.rules,
            require_human_review: review,
            allow_review: allow,
          },
          reason,
        },
      })
      notify('新的策略版本已保存')
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel padded>
      <form className="edu-form" onSubmit={save}>
        <div className="edu-inline justify-between">
          <h2>
            {p.policy_type === 'global'
              ? '组织全局策略'
              : `课程 ${p.course_id} 策略`}
          </h2>
          <StatusPill>第 {p.version} 版</StatusPill>
        </div>
        <p className="edu-small edu-muted">
          新策略只影响后续内容与布置，不改写已有作答的规则。
        </p>
        <label className="edu-inline">
          <input
            className="!h-4 !w-4"
            type="checkbox"
            checked={review}
            onChange={(e) => setReview(e.target.checked)}
          />
          要求教师复核
        </label>
        <label className="edu-inline">
          <input
            className="!h-4 !w-4"
            type="checkbox"
            checked={allow}
            onChange={(e) => setAllow(e.target.checked)}
          />
          允许学生申请复核
        </label>
        <p className="edu-small edu-muted">
          研究候选自带的强制复核不受此开关取消。
        </p>
        <Field label="策略更新理由">
          <input
            className={INPUT}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            required
          />
        </Field>
        {error && <Notice error>{error}</Notice>}
        <div>
          <Button type="submit" loading={busy}>
            保存为新版本
          </Button>
        </div>
      </form>
    </Panel>
  )
}
