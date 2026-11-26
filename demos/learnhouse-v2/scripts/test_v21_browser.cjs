// Real browser/component check with synthetic API responses; no live login or DB.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.QA_WEB_URL || 'http://localhost:13301';
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
assert.equal(fixture.data_source, 'synthetic_demo_data');
assert.equal(fixture.learning_evidence_eligible, false);
const output = process.argv[3] || '/tmp/education-v21-browser';
fs.mkdirSync(output, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const checks = [];
  const errors = [];
  let role = 'teacher', failPreview = false, previewCalls = 0;
  let bindingReads = 0, mode = 'grant', sentGrant;
  const binding = '11111111-1111-4111-8111-111111111111';
  const decision = '22222222-2222-4222-8222-222222222222';
  const grant = '33333333-3333-4333-8333-333333333333';
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  await context.route('**/*', (route) => {
    const url = new URL(route.request().url());
    return url.origin === new URL(base).origin ? route.continue() : route.abort();
  });
  await context.route('**/api/**', async (route) => {
    const request = route.request(), p = new URL(request.url()).pathname;
    const send = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (p.endsWith('/education/session')) return send({ principal: {
      role_views: [role === 'admin' ? 'administrator' : role], display_name: '合成演示账号', user_id: 101, org_id: 1,
    }, scopes: [] });
    if (p.endsWith('/education/courses')) return send({ items: [{ id: 1, name: '合成演示课程' }] });
    if (p.endsWith('/education/research-preview')) {
      previewCalls++;
      return failPreview ? send({ detail: { message: '合成测试：样例暂不可用' } }, 503) : send(fixture);
    }
    if (p.endsWith(`/exercises/${binding}`)) {
      bindingReads++;
      return send({ id: binding, title: '合成推荐练习', status: 'assigned', course_id: 1, max_attempts: 1,
        recipient: { id: 'recipient-preview', attempts: [], active_grants: [{ id: 'obsolete-first-grant' }] },
        etag: 'fixture-etag', question_content: { questions: [] },
        start_option: { kind: mode, attempt_id: null,
          resubmission_grant_id: mode === 'normal' ? null : (bindingReads === 1 ? 'old-preview-grant' : grant) },
      });
    }
    if (p.endsWith(`/bindings/${binding}/attempts`) && request.method() === 'POST') {
      sentGrant = request.postDataJSON().resubmission_grant_id;
      // Leave the component on its current page after observing the safe payload.
      return send({ detail: { message: '合成测试：请求已捕获，未创建作答' } }, 409);
    }
    return send({ detail: { message: '合成测试未配置此接口' } }, 404);
  });
  try {
    await page.goto(base + '/education/teacher/integration', { waitUntil: 'domcontentloaded', timeout: 120000 });
    await page.getByLabel('选择演示场景').waitFor({ timeout: 120000 });
    assert.equal(await page.getByText('合成数据 · 只读').count(), 1);
    checks.push('teacher_preview_synthetic_notice');
    for (const scenario of fixture.scenarios) {
      await page.getByLabel('选择演示场景').selectOption(scenario.id);
      await page.waitForFunction((count) => document.querySelectorAll('ol[aria-label="样例事件顺序"] > li').length === count, scenario.actions.length);
      assert.equal(await page.locator('ol[aria-label="样例事件顺序"] > li').count(), scenario.actions.length);
    }
    checks.push('three_scenarios_match_actual_python_output');
    assert.equal(await page.getByRole('link', { name: '打开练习准备' }).getAttribute('href'), '/education/teacher/courses/1/generate');
    checks.push('generation_navigation');
    await page.screenshot({ path: path.join(output, 'teacher-preview-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({ path: path.join(output, 'teacher-preview-mobile.png'), fullPage: true });
    checks.push('mobile_no_horizontal_overflow');
    await page.setViewportSize({ width: 1440, height: 1000 });

    failPreview = true;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '重新加载' }).waitFor();
    failPreview = false;
    await page.getByRole('button', { name: '重新加载' }).click();
    await page.getByLabel('选择演示场景').waitFor();
    checks.push('preview_failure_retry');

    role = 'student';
    const prior = previewCalls;
    await page.goto(base + '/education/student/integration', { waitUntil: 'domcontentloaded' });
    await page.getByText('当前账号没有访问此演示页面的权限。').waitFor();
    assert.equal(previewCalls, prior);
    checks.push('student_preview_blocked_in_ui');

    for (const nextMode of ['grant', 'normal']) {
      mode = nextMode; bindingReads = 0; sentGrant = undefined;
      await page.goto(base + `/education/student/recommended/1/101/${decision}/${binding}`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: '开始新的尝试' }).click();
      await page.getByText('合成测试：请求已捕获，未创建作答').waitFor();
      assert(bindingReads >= 2);
      assert.equal(sentGrant, mode === 'grant' ? grant : null);
      checks.push(mode === 'grant' ? 'recommendation_uses_refreshed_grant' : 'normal_attempt_ignores_unrelated_grant');
    }

    role = 'admin';
    await page.goto(base + '/education/admin/integration', { waitUntil: 'domcontentloaded' });
    await page.getByLabel('选择演示场景').waitFor();
    assert.equal(await page.getByRole('link', { name: '查看任务与能力' }).getAttribute('href'), '/education/admin/operations');
    checks.push('admin_synthetic_preview');
    assert.deepEqual(errors, []);
    checks.push('no_browser_page_errors');
    const report = { passed: checks.length, checks, scope: 'Next.js browser with synthetic API responses; no real login/database/model', page_errors: errors };
    fs.writeFileSync(path.join(output, 'browser-results.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
