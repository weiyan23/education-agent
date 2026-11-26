"""HTTP adapter for the real PostgreSQL-backed education demo."""
from __future__ import annotations
import asyncio
from contextlib import aclosing
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from functools import wraps
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, HTTPException
from sqlalchemy.exc import DBAPIError
from src.core.events.database import get_db_session
from src.education.security import require_actor, require_current_actor, require_course, require_admin, fail
from src.education import recommendation_import as ri
from src.education.recommendation_mapping import resolve_exercises
from src.education import business as b
from src.db.courses.courses import Course
from src.db.courses.chapters import Chapter, LockType
from src.db.courses.activities import Activity, ActivityTypeEnum, ActivitySubTypeEnum, ActivityLockType
from src.db.courses.assignments import Assignment, GradingTypeEnum

router=APIRouter()


def safe(fn):
    @wraps(fn)
    async def wrapped(*args,**kwargs):
        try:return await fn(*args,**kwargs)
        except DBAPIError as exc:
            db=kwargs.get('db')
            if db is not None:await db.rollback()
            # Never expose SQL, bound answers, credentials or internal trace.
            sqlstate=getattr(exc.orig,'sqlstate',None) or getattr(exc.orig,'pgcode',None)
            if sqlstate=='42501':fail('SCOPE_DENIED',403,'当前课程或班级权限已失效')
            fail('BUSINESS_CONSTRAINT',409,'操作与当前数据状态冲突，请刷新后重试')
    return wrapped


async def payload(request,schema):
    try:data=await request.json()
    except (ValueError,UnicodeError):fail('INVALID_REQUEST',422,'请求必须为JSON')
    return b.validate(schema,data)


async def policy(db,actor,course_id,policy_id):
    p=b.must(await b.one(db,'SELECT * FROM ea.policy_version WHERE id=:id AND org_id=:org',id=policy_id,org=actor.org_id))
    if p['course_id']!=course_id:fail('SCOPE_DENIED',403,'课程策略不匹配')
    return p


async def current_policy(db,actor,course_id,policy_id):
    # Use the same organization policy lock as policy administration. Existing
    # attempts and their grading paths never call this: their rules stay frozen.
    await b.execute(db,'SELECT pg_advisory_xact_lock(hashtextextended(:key,0))',key='policy:'+str(actor.org_id))
    p=b.must(await b.one(db,"SELECT * FROM ea.policy_version WHERE org_id=:org AND course_id=:course AND policy_type='course' ORDER BY version DESC,created_at DESC LIMIT 1",org=actor.org_id,course=course_id))
    if str(p['id'])!=str(policy_id):fail('POLICY_VERSION_CONFLICT',409,'请刷新并使用当前课程策略；历史作答仍使用原冻结策略')
    global_policy=b.must(await b.one(db,"SELECT id FROM ea.policy_version WHERE org_id=:org AND course_id IS NULL AND policy_type='global' ORDER BY version DESC,created_at DESC LIMIT 1",org=actor.org_id))
    if p['parent_policy_id']!=global_policy['id']:fail('POLICY_PARENT_STALE',409,'全局策略已更新，请先追加与当前全局规则对齐的课程策略；历史作答不受影响')
    return b.policy_rules(p)


async def concepts(db,actor,course_id,ids):
    if not ids or len(ids)>50 or len(set(ids))!=len(ids):fail('INVALID_KNOWLEDGE_POINTS',422)
    for item in ids:
        if not await b.one(db,'SELECT id FROM ea.knowledge_point WHERE id=:id AND course_id=:course AND org_id=:org',id=item,course=course_id,org=actor.org_id):fail('SCOPE_DENIED',403,'知识点不属于当前课程')


async def content_valid(db,actor,course_id,content,restricted):
    await concepts(db,actor,course_id,content.get('knowledge_point_ids',[]))
    questions=content.get('questions',[]);ids=[q['item_id'] for q in questions]
    if len(ids)!=len(set(ids)) or len(ids)>50:fail('INVALID_CONTENT',422,'题号必须唯一且最多50题')
    for q in questions:
        if not q.get('prompt','').strip() or q['maximum_score']<=0:fail('INVALID_CONTENT',422)
        if not set(q.get('knowledge_point_ids',[])).issubset(set(content['knowledge_point_ids'])):fail('INVALID_CONTENT',422)
    answer_ids=[a['item_id'] for a in restricted.get('answers',[])]
    if len(answer_ids)!=len(set(answer_ids)) or not set(answer_ids).issubset(set(ids)):fail('INVALID_ANSWER_KEY',422)
    keys={a['item_id']:a['value'] for a in restricted.get('answers',[])}
    if not objective_keys_valid(questions,keys,require_all=False):fail('INVALID_ANSWER_KEY',422,'客观题答案类型或选项不匹配')
    refs=content.get('source_refs',[])
    if len(refs)>50:fail('INVALID_SOURCE_REFS',422)
    for ref in refs:
        source=await b.resource_version(db,actor,ref['resource_version_id'],'content.read')
        if source['course_id']!=course_id:fail('SCOPE_DENIED',403,'来源必须属于当前课程')
        if ref['content_hash']!=source['content_hash']:fail('SOURCE_HASH_MISMATCH',409,'引用资料内容哈希不匹配')


def objective_keys_valid(questions,keys,require_all=True):
    for question in questions:
        item=question['item_id'];kind=question['type']
        if kind not in ('boolean','single_choice'):continue
        if item not in keys:
            if require_all:return False
            continue
        value=keys[item]
        if kind=='boolean' and not isinstance(value,bool):return False
        if kind=='single_choice':
            choices=[c['key'] for c in question.get('choices',[])]
            if len(choices)<2 or len(choices)!=len(set(choices)) or not isinstance(value,str) or value not in choices:return False
    return True


async def deterministic_checks(db,actor,v):
    content=v['content'];qs=content.get('questions',[])
    restricted=v['restricted_content'] or {'answers':[],'grading_notes':''}
    schema=True
    try:b.validate('ResourceContent',content);b.validate('RestrictedContent',restricted)
    except HTTPException:schema=False
    keys={a['item_id']:a['value'] for a in restricted.get('answers',[])}
    scope=True;source_ready=v['processing_state']=='ready'
    ids=content.get('knowledge_point_ids',[])
    if not ids or len(ids)!=len(set(ids)):scope=False
    for point in ids:
        if not await b.one(db,'SELECT id FROM ea.knowledge_point WHERE id=:id AND org_id=:org AND course_id=:course',id=point,org=actor.org_id,course=v['course_id']):scope=False
    for question in qs:
        if not set(question.get('knowledge_point_ids',[])).issubset(set(ids)):scope=False
    for ref in content.get('source_refs',[]):
        source=await b.one(db,'SELECT v.content_hash,v.processing_state,r.archived_at FROM ea.resource_version v JOIN ea.resource r ON r.id=v.resource_id WHERE v.id=:id AND v.org_id=:org AND v.course_id=:course',id=ref['resource_version_id'],org=actor.org_id,course=v['course_id'])
        if not source:scope=False;source_ready=False
        elif source['processing_state']!='ready' or source['archived_at'] or source['content_hash']!=ref['content_hash']:source_ready=False
    if v.get('source_version_id'):
        source=await b.one(db,'SELECT processing_state FROM ea.resource_version WHERE id=:id AND org_id=:org AND course_id=:course',id=v['source_version_id'],org=actor.org_id,course=v['course_id'])
        if not source:scope=False;source_ready=False
        elif source['processing_state']!='ready':source_ready=False
    truth=True
    if v['generation_mode']=='rule_template':
        expected=[('q1','已知 int a = 7, b = 3，表达式 a > b && b > 0 的值是否为 true？',(7>3 and 3>0)),('q2','已知 boolean enabled = false，表达式 !enabled || (5 < 2) 的值是否为 true？',((not False) or (5<2)))]
        truth=len(qs)==len(expected) and all(q['item_id']==item and q['prompt']==prompt and q['type']=='boolean' and keys.get(item) is value for q,(item,prompt,value) in zip(qs,expected))
    return {'schema':schema,'source_ready':source_ready,'scope':scope,'answer_consistency':objective_keys_valid(qs,keys),'rubric_bounds':all(q['maximum_score']>0 for q in qs),'boolean_truth_table':truth,'content_present':bool(content.get('body_text') or qs),'unique_question_ids':len({q['item_id'] for q in qs})==len(qs),'no_execution':True}


def required_checks_pass(rules,checks):
    return all(checks.get(name) is True for name in rules['required_checks']) and all(checks.values())


def validate_rubric(content, rubric):
    questions={q['item_id']:q for q in content['questions']}
    criteria=rubric['criteria'];criterion_ids=[c['criterion_id'] for c in criteria]
    if not criteria or len(criterion_ids)!=len(set(criterion_ids)) or any(not c for c in criterion_ids):
        fail('INVALID_RUBRIC',422,'量规条目编号必须唯一且非空')
    totals={item:Decimal('0') for item in questions}
    for criterion in criteria:
        item=criterion['item_id']
        if item not in questions or not set(criterion['knowledge_point_ids']).issubset(set(questions[item]['knowledge_point_ids'])):
            fail('INVALID_RUBRIC',422,'量规条目必须对应冻结练习中的题目及知识点')
        totals[item]+=Decimal(str(criterion['maximum_score']))
    if any(totals[item]!=Decimal(str(question['maximum_score'])) for item,question in questions.items()) or sum(totals.values())!=Decimal(str(rubric['maximum_score'])):
        fail('RUBRIC_SCALE_MISMATCH',422,'量规各题分值及总分必须与冻结练习一致')


@router.get('/courses')
@safe
async def list_courses(request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    return await b.courses(db,actor)


@router.get('/courses/{course_id}')
@safe
async def read_course(course_id:int,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    return await b.get_course(db,actor,course_id)


@router.patch('/courses/{course_id}')
@safe
async def patch_course(course_id:int,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'CoursePatch');c=await b.course_row(db,actor,course_id,'course.edit')
    async def change():
        b.matching(request,b.course_etag(c))
        values={k:data.get(k,c.get(k)) for k in ['name','description','learnings','published']}
        if 'chapter_order' in data or 'policy_version_id' in data:fail('CAPABILITY_NOT_IMPLEMENTED',409,'本版课程编辑支持名称、说明和学习目标；编排与策略引用暂不开放')
        await b.execute(db,'UPDATE public.course SET name=:name,description=:description,learnings=:learnings,published=:published,update_date=:updated WHERE id=:id AND org_id=:org',**values,updated=datetime.now(timezone.utc).isoformat(),id=course_id,org=actor.org_id)
        return {'course_id':course_id}
    await b.write_once(db,actor,request,'course.update',data,course_id,change)
    return await b.get_course(db,actor,course_id)


@router.post('/courses/{course_id}/resources')
@safe
async def create_source(course_id:int,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'SourceTextCreate');c=await b.course_row(db,actor,course_id,'content.edit')
    await policy(db,actor,course_id,data['policy_version_id']);await concepts(db,actor,course_id,data['knowledge_point_ids'])
    async def change():
        await current_policy(db,actor,course_id,data['policy_version_id'])
        b.matching(request,b.course_etag(c));resource=str(uuid4());version=str(uuid4())
        content={'title':data['title'],'body_text':data['text'],'questions':[],'knowledge_point_ids':data['knowledge_point_ids'],'source_refs':[]}
        await b.execute(db,"INSERT INTO ea.resource(id,org_id,course_id,kind,title,owner_id) VALUES(:id,:org,:course,'source_text',:title,:actor)",id=resource,org=actor.org_id,course=course_id,title=data['title'],actor=actor.principal_id)
        await b.execute(db,"INSERT INTO ea.resource_version(id,org_id,course_id,resource_id,version,policy_version_id,content,content_hash,processing_state,generation_mode,provenance,forced_review,created_by) VALUES(:id,:org,:course,:resource,1,:policy,CAST(:content AS jsonb),:hash,'ready','upload','demo',true,:actor)",id=version,org=actor.org_id,course=course_id,resource=resource,policy=data['policy_version_id'],content=b.canonical(content),hash=b.digest(content),actor=actor.principal_id)
        return {'resource_id':resource,'resource_version_id':version,'task_id':None}
    return await b.write_once(db,actor,request,'source.create',data,course_id,change)


@router.get('/resources/{resource_id}')
@safe
async def read_resource(resource_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    return await b.get_resource(db,actor,resource_id)


@router.post('/resources/{resource_id}/versions')
@safe
async def new_version(resource_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'ResourceVersionCreate');v=b.must(await b.one(db,'SELECT * FROM ea.resource_version WHERE resource_id=:id AND org_id=:org ORDER BY version DESC LIMIT 1',id=resource_id,org=actor.org_id));course=v['course_id']
    await require_course(db,actor,course,action='content.edit');await policy(db,actor,course,data['policy_version_id']);await content_valid(db,actor,course,data['content'],data['restricted_content'])
    async def change():
        await current_policy(db,actor,course,data['policy_version_id'])
        await b.execute(db,'SELECT id FROM ea.resource WHERE id=:id FOR UPDATE',id=resource_id)
        current=b.must(await b.one(db,'SELECT * FROM ea.resource_version WHERE resource_id=:id ORDER BY version DESC LIMIT 1',id=resource_id))
        b.matching(request,b.etag(current['version']))
        if data['expected_version']!=current['version']:fail('VERSION_CONFLICT',409)
        new=str(uuid4())
        await b.execute(db,"INSERT INTO ea.resource_version(id,org_id,course_id,resource_id,version,previous_version_id,source_version_id,policy_version_id,content,restricted_content,content_hash,processing_state,generation_mode,provenance,source_manifest,forced_review,created_by) VALUES(:id,:org,:course,:resource,:version,:previous,:source,:policy,CAST(:content AS jsonb),CAST(:restricted AS jsonb),:hash,'ready','teacher',:provenance,CAST(:manifest AS jsonb),true,:actor)",id=new,org=actor.org_id,course=course,resource=resource_id,version=current['version']+1,previous=current['id'],source=current['source_version_id'],policy=data['policy_version_id'],content=b.canonical(data['content']),restricted=b.canonical(data['restricted_content']),hash=b.digest({'content':data['content'],'restricted_content':data['restricted_content']}),provenance=current['provenance'],manifest=b.canonical(current['source_manifest']),actor=actor.principal_id)
        return {'resource_id':str(resource_id),'resource_version_id':new,'task_id':None}
    return await b.write_once(db,actor,request,'resource.version',data,course,change)


@router.post('/courses/{course_id}/generation-runs')
@safe
async def generate(course_id:int,request:Request,background_tasks:BackgroundTasks,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'GenerationRequest');c=await b.course_row(db,actor,course_id,'content.generate')
    v=await b.resource_version(db,actor,data['source_version_id'],'content.read')
    if v['course_id']!=course_id:fail('SCOPE_DENIED',403)
    if v['processing_state']!='ready' or v['archived_at']:fail('SOURCE_NOT_READY',409,'只能使用已就绪且未归档的资料')
    selected=await policy(db,actor,course_id,data['policy_version_id']);rules=b.policy_rules(selected)
    await concepts(db,actor,course_id,data['knowledge_point_ids'])
    if data['mode'] not in rules['allowed_generation_modes']:fail('GENERATION_MODE_DISABLED',403,'课程策略禁止此生成方式')
    if data['budget_limit']>rules['maximum_task_budget']:fail('POLICY_BUDGET_EXCEEDED',422)
    if data['mode']=='research_generator':
        await require_course(db,actor,course_id,action='content.generate',student_id=data['target_student_id'])
        if data.get('run_mode') == 'execute':
            env_file = b.generation_env_file()
            if not env_file:
                fail('RESEARCH_NOT_CONNECTED',503,'请在服务端配置模型')
            if data.get('confirm_paid') is not True:
                fail('EXECUTION_NOT_AUTHORIZED',403,'请确认本次模型调用')
            if data['budget_limit'] <= 0 or data['max_attempts'] != 1:
                fail('INVALID_GENERATION_BUDGET',422,'请输入课程策略允许的预算，任务尝试次数为一次')
            return await generate_research(course_id,request,actor,db,data,c,v,env_file,background_tasks)
        if data.get('run_mode') != 'fixture' or os.getenv('EA_GENERATION_FIXTURE_COURSE_ID') != str(course_id):
            fail('RESEARCH_NOT_CONNECTED',503,'仅配置好的联调课程可使用合成生成')
        return await generate_research(course_id,request,actor,db,data,c,v)
    if data['budget_limit']!=0:fail('INVALID_RULE_BUDGET',422,'规则模板不消耗模型预算')
    async def change():
        await current_policy(db,actor,course_id,data['policy_version_id'])
        b.matching(request,b.course_etag(c));task=str(uuid4());resource=str(uuid4());version=str(uuid4())
        content={'title':'Java 布尔表达式判断练习','body_text':'固定 Java 布尔表达式规则模板；所选资料仅建立来源关联，未进行语义生成。请选择每个表达式的结果。','questions':[{'item_id':'q1','type':'boolean','prompt':'已知 int a = 7, b = 3，表达式 a > b && b > 0 的值是否为 true？','maximum_score':50,'knowledge_point_ids':data['knowledge_point_ids'],'language':'java'},{'item_id':'q2','type':'boolean','prompt':'已知 boolean enabled = false，表达式 !enabled || (5 < 2) 的值是否为 true？','maximum_score':50,'knowledge_point_ids':data['knowledge_point_ids'],'language':'java'}],'knowledge_point_ids':data['knowledge_point_ids'],'source_refs':[{'resource_version_id':str(v['id']),'locator':'课程资料','content_hash':v['content_hash']}]}
        restricted={'answers':[{'item_id':'q1','value':True,'rationale':'两个比较均为 true，逻辑与为 true。'},{'item_id':'q2','value':True,'rationale':'!false 为 true，逻辑或的结果为 true。'}],'grading_notes':'仅对结构化布尔答案作确定性判分；不执行代码。'}
        await b.execute(db,"INSERT INTO ea.task_run(id,org_id,course_id,requested_by,operation,idempotency_key,input_hash,input_refs,result_refs,status,attempt_count,max_attempts,completed_at) VALUES(:id,:org,:course,:actor,'generate',:key,:hash,CAST(:input AS jsonb),CAST(:refs AS jsonb),'succeeded',1,1,now())",id=task,org=actor.org_id,course=course_id,actor=actor.principal_id,key=request.headers['idempotency-key'],hash=b.digest(data),input=b.canonical({'mode':'rule_template','source_version_id':str(v['id']),'policy_version_id':data['policy_version_id']}),refs=b.canonical({'resource_id':resource,'resource_version_id':version}))
        await b.execute(db,"INSERT INTO ea.resource(id,org_id,course_id,kind,title,owner_id) VALUES(:id,:org,:course,'exercise',:title,:actor)",id=resource,org=actor.org_id,course=course_id,title=content['title'],actor=actor.principal_id)
        await b.execute(db,"INSERT INTO ea.resource_version(id,org_id,course_id,resource_id,version,source_version_id,generation_task_id,policy_version_id,content,restricted_content,content_hash,processing_state,generation_mode,provenance,source_manifest,forced_review,created_by) VALUES(:id,:org,:course,:resource,1,:source,:task,:policy,CAST(:content AS jsonb),CAST(:restricted AS jsonb),:hash,'ready','rule_template','demo',CAST(:manifest AS jsonb),:forced,:actor)",id=version,org=actor.org_id,course=course_id,resource=resource,source=v['id'],task=task,policy=data['policy_version_id'],content=b.canonical(content),restricted=b.canonical(restricted),hash=b.digest({'content':content,'restricted_content':restricted}),manifest=b.canonical({'rule_template':'java-boolean-v1','model_called':False}),forced=rules['require_human_review'] or not rules['allow_rule_auto_publish'],actor=actor.principal_id)
        return {'task_id':task,'resource_id':resource,'resource_version_id':version}
    result=await b.write_once(db,actor,request,'generation.rule',data,course_id,change)
    return await read_task(UUID(result['task_id']),request,actor,db)


async def generate_research(course_id, request, actor, db, data, course, source, env_file=None, background_tasks=None):
    fixture = env_file is None
    if fixture and (data['budget_limit'] != 0 or data['max_attempts'] != 1 or data['max_calls'] != 4):
        fail('INVALID_FIXTURE_BUDGET',422,'合成联调固定为零费用、一次任务、四条固定响应')
    demo = Path(__file__).resolve().parents[6]
    integration = str(demo / 'integration')
    if integration not in sys.path:
        sys.path.append(integration)
    from generation_candidate import CandidateError, build_real_snapshot, run_fixture_candidate, run_real_candidate

    task = str(uuid4())
    targets = {}
    snapshot = None
    student_id = f"v2-{actor.org_id}-{data['target_student_id']}"
    mode = data['run_mode']
    operation = 'generation.' + mode

    async def prepare():
        nonlocal snapshot
        await current_policy(db,actor,course_id,data['policy_version_id'])
        b.matching(request,b.course_etag(course))
        for item in data['knowledge_point_ids']:
            row = b.must(await b.one(db,'''SELECT code FROM ea.knowledge_point
                WHERE id=:id AND course_id=:course AND org_id=:org FOR SHARE''',
                id=item,course=course_id,org=actor.org_id))
            if row['code'] in targets:
                fail('INVALID_KNOWLEDGE_POINTS',422,'请选择知识点的单一版本')
            targets[row['code']] = item
        if not fixture:
            try:
                snapshot = build_real_snapshot(
                    student_id=student_id, targets=targets, requirement=data['requirement'],
                    source_text=source['content'].get('body_text',''), source_id=str(source['id']),
                    source_hash=source['content_hash'], source_kind='real_student',
                )
            except CandidateError as exc:
                fail(str(exc),422,'请选择受支持的 Java 知识点及有效的文本资料')

    async def reserve():
        await prepare()
        inputs = {'mode':'research_generator','run_mode':mode,'source_version_id':str(source['id']),
                  'policy_version_id':data['policy_version_id'],'max_calls':data['max_calls']}
        await b.execute(db,'''INSERT INTO ea.task_run
            (id,org_id,course_id,requested_by,operation,idempotency_key,input_hash,input_refs,status,
             attempt_count,max_attempts,budget_limit,lease_until)
            VALUES(:id,:org,:course,:actor,'generate',:key,:hash,CAST(:input AS jsonb),'running',1,1,
                   :budget,now()+interval '12 minutes')''', id=task,org=actor.org_id,course=course_id,
            actor=actor.principal_id,key=mode+':'+request.headers['idempotency-key'],
            hash=b.digest(data),input=b.canonical(inputs),budget=data['budget_limit'])
        return {'task_id':task}

    def current_scope():
        if fixture:
            return os.getenv('EA_GENERATION_FIXTURE_COURSE_ID') == str(course_id)
        return b.generation_env_file() == env_file

    async def run():
        common = dict(actor_id=str(actor.principal_id), course_id=str(course_id), job_id=task,
                      targets=targets, source_id=str(source['id']), source_hash=source['content_hash'])
        if not fixture:
            return await asyncio.to_thread(run_real_candidate, Path.home() / '.local/state/education-v2-generation',
                                           current_scope, snapshot=snapshot, env_file=env_file,
                                           max_calls=data['max_calls'], **common)
        return await asyncio.to_thread(run_fixture_candidate, demo / '.runtime' / 'generation-fixtures',
                                       current_scope, student_id=student_id, requirement=data['requirement'], **common)

    async def authorize_generation(db):
        active_actor = await require_current_actor(db, actor)
        await current_policy(db,active_actor,course_id,data['policy_version_id'])
        await require_course(db,active_actor,course_id,action='content.generate',student_id=data['target_student_id'])
        if not current_scope():
            fail('GENERATION_SCOPE_CHANGED',409,'生成配置已失效')
        latest = await b.resource_version(db,active_actor,source['id'],'content.read')
        if latest['content_hash'] != source['content_hash'] or latest['archived_at'] or latest['processing_state'] != 'ready':
            fail('SOURCE_NOT_READY',409,'生成期间资料状态已变化')
        return active_actor

    async def import_candidate(db, candidate):
        active_actor = await authorize_generation(db)
        resource = str(uuid4())
        version_ids = [str(uuid4()) for _ in candidate.versions]
        refs = {'task_id':task,'resource_id':resource,'resource_version_id':version_ids[-1]}
        await b.execute(db,'''UPDATE ea.task_run SET status='succeeded',lease_until=NULL,
            completed_at=now(),updated_at=now(),result_refs=CAST(:refs AS jsonb) WHERE id=:id''',
            id=task,refs=b.canonical(refs))
        await b.execute(db,'''INSERT INTO ea.resource(id,org_id,course_id,kind,title,owner_id)
            VALUES(:id,:org,:course,'exercise',:title,:actor)''',
            id=resource,org=actor.org_id,course=course_id,actor=actor.principal_id,
            title=candidate.versions[-1].content['title'])
        for index, version in enumerate(candidate.versions):
            await content_valid(db,active_actor,course_id,version.content,version.restricted)
            manifest = {'provenance':candidate.provenance,'model_called':not fixture,
                        'artifact_sha256':candidate.artifact_hash,'question_sha256':version.question_hash}
            await b.execute(db,'''INSERT INTO ea.resource_version
                (id,resource_id,org_id,course_id,version,previous_version_id,source_version_id,policy_version_id,
                 generation_task_id,content,restricted_content,content_hash,processing_state,generation_mode,
                 provenance,source_manifest,forced_review,created_by)
                VALUES(:id,:resource,:org,:course,:version,:previous,:source,:policy,:task,CAST(:content AS jsonb),
                       CAST(:restricted AS jsonb),:hash,'ready','research_generator',:provenance,
                       CAST(:manifest AS jsonb),true,:actor)''',
                id=version_ids[index],resource=resource,org=actor.org_id,course=course_id,version=index+1,
                previous=version_ids[index-1] if index else None,source=source['id'],
                policy=data['policy_version_id'],task=task,content=b.canonical(version.content),
                restricted=b.canonical(version.restricted),
                hash=b.digest({'content':version.content,'restricted_content':version.restricted}),
                manifest=b.canonical(manifest),actor=actor.principal_id,provenance=candidate.provenance)
        return refs

    async def finish(db):
        try:
            await authorize_generation(db)
            state = await b.one(db,'SELECT status FROM ea.task_run WHERE id=:id',id=task)
            await db.rollback()
            if state['status'] != 'running':
                return
            candidate = await run()
            row = await b.one(db,'SELECT status FROM ea.task_run WHERE id=:id FOR UPDATE',id=task)
            if row['status'] == 'running':
                await import_candidate(db, candidate)
            await db.commit()
        except (CandidateError, HTTPException, DBAPIError) as exc:
            await db.rollback()
            code = str(exc) if isinstance(exc,CandidateError) else 'GENERATION_IMPORT_BLOCKED'
            await b.execute(db,'''UPDATE ea.task_run SET status='failed',lease_until=NULL,
                completed_at=now(),updated_at=now(),error_code=:code WHERE id=:id AND status='running' ''',
                id=task,code=code)
            await db.commit()

    async def finish_in_background():
        async with aclosing(get_db_session()) as sessions:
            await finish(await anext(sessions))

    reserved = await b.write_once(db,actor,request,operation,data,course_id,reserve)
    if reserved['task_id'] != task:
        return await read_task(UUID(reserved['task_id']),request,actor,db)
    if fixture:
        await finish(db)
    else:
        background_tasks.add_task(finish_in_background)
    return await read_task(UUID(task),request,actor,db)


@router.get('/tasks/{task_id}')
@safe
async def read_task(task_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    t=b.must(await b.one(db,'SELECT * FROM ea.task_run WHERE id=:id AND org_id=:org',id=task_id,org=actor.org_id))
    if actor.role=='admin':require_admin(actor,'operations.manage')
    elif actor.role=='teacher':await require_course(db,actor,t['course_id'],action='content.generate')
    else:fail('SCOPE_DENIED',403)
    result={k:t[k] for k in ['id','org_id','course_id','operation','status','execution_epoch','attempt_count','max_attempts','budget_limit','budget_spent','error_code','result_refs','created_at','updated_at']}
    if t['status']=='running' and t['lease_until'] and t['lease_until'] <= datetime.now(timezone.utc):
        result.update(status='failed',error_code='JOB_OUTCOME_UNKNOWN')
    if actor.role=='admin':result['result_refs']={}
    can_cancel=result['status'] in ('queued','running')
    result.update(capability_status=None,run_mode=t['input_refs'].get('run_mode'),output_kind='none' if actor.role=='admin' else ('resource_candidate' if t['operation']=='generate' else 'projection'),can_cancel=can_cancel,can_retry=False,retry_block_reason='真实研究任务恢复尚未接通',cancellation_scope='prevent_result_application' if can_cancel else None,etag=b.etag(t['execution_epoch']))
    return b.clean(result)


@router.post('/tasks/{task_id}/commands')
@safe
async def task_command(task_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'TaskCommand');task=await read_task(task_id,request,actor,db)
    if data['command']=='retry':fail('RESEARCH_RETRY_UNAVAILABLE',409,'本版没有可恢复的异步研究任务；不能以新任务绕过预算')
    async def change():
        current=b.must(await b.one(db,'SELECT * FROM ea.task_run WHERE id=:id AND org_id=:org FOR UPDATE',id=task_id,org=actor.org_id))
        b.matching(request,b.etag(current['execution_epoch']))
        if data['expected_execution_epoch']!=current['execution_epoch']:fail('VERSION_CONFLICT',409)
        if current['status'] not in ('queued','running'):fail('TASK_TERMINAL',409,'已结束任务不能取消')
        await b.execute(db,"UPDATE ea.task_run SET status='cancelled',execution_epoch=execution_epoch+1,cancelled_at=now(),completed_at=now(),lease_until=NULL,updated_at=now() WHERE id=:id",id=task_id)
        return {'task_id':str(task_id)}
    await b.write_once(db,actor,request,'task.cancel',data,task['course_id'],change)
    return await read_task(task_id,request,actor,db)


@router.post('/resource-versions/{resource_version_id}/checks')
@safe
async def check_version(resource_version_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'CheckRequest');action='content.review' if data['check_kind']=='human_review' else 'content.edit'
    v=await b.resource_version(db,actor,resource_version_id,action);await policy(db,actor,v['course_id'],data['policy_version_id'])
    async def change():
        rules=await current_policy(db,actor,v['course_id'],data['policy_version_id'])
        b.matching(request,b.etag(v['version']))
        if data['check_kind']=='human_review':
            check=b.must(await b.one(db,"SELECT id,checks FROM ea.content_check WHERE id=:id AND resource_version_id=:v AND policy_version_id=:policy AND check_kind='deterministic' AND verdict='pass'",id=data['validation_id'],v=resource_version_id,policy=data['policy_version_id']))
            if not required_checks_pass(rules,check['checks']):fail('POLICY_CHECKS_REQUIRED',409,'请按当前策略重新执行检查')
            verdict=data['verdict'];reason=data['reason'];checks={'validation_id':str(check['id'])}
        else:
            checks=await deterministic_checks(db,actor,v)
            verdict='pass' if required_checks_pass(rules,checks) else 'fail';reason='结构化确定性检查，不证明教学效果；真值检查只适用于已知固定模板，其余材料语义由教师确认'
        row=b.must(await b.one(db,'INSERT INTO ea.content_check(org_id,course_id,resource_version_id,policy_version_id,check_kind,verdict,checks,checker_version,actor_id,reason) VALUES(:org,:course,:v,:policy,:kind,:verdict,CAST(:checks AS jsonb),:version,:actor,:reason) RETURNING id',org=actor.org_id,course=v['course_id'],v=resource_version_id,policy=data['policy_version_id'],kind=data['check_kind'],verdict=verdict,checks=b.canonical(checks),version='demo-structure-v1' if data['check_kind']=='deterministic' else 'human-review-v1',actor=actor.principal_id,reason=reason))
        return {'check_id':str(row['id'])}
    refs=await b.write_once(db,actor,request,'content.check',data,v['course_id'],change)
    result=b.must(await b.one(db,'SELECT id,resource_version_id,policy_version_id,check_kind,verdict,checks,checker_version,reason,created_at FROM ea.content_check WHERE id=:id',id=refs['check_id']))
    result['checks']=[{'code':code,'passed':bool(value) if isinstance(value,bool) else result['verdict'] in ('pass','approve'),'message':code if isinstance(value,bool) else str(value)} for code,value in result['checks'].items()]
    if v['generation_mode']!='rule_template':
        for item in result['checks']:
            if item['code']=='boolean_truth_table':item['message']='不适用：不是已知固定规则模板；不表示已验证题干语义'
    result['etag']=b.etag(v['version'])
    return b.clean(result)


@router.post('/resource-versions/{resource_version_id}/publication-events')
@safe
async def publish(resource_version_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'PublicationRequest');v=await b.resource_version(db,actor,resource_version_id,'content.publish')
    await require_course(db,actor,v['course_id'],action='content.publish',class_id=data['usergroup_id']);await policy(db,actor,v['course_id'],data['policy_version_id'])
    async def change():
        rules=await current_policy(db,actor,v['course_id'],data['policy_version_id'])
        b.matching(request,b.etag(v['version']))
        await b.execute(db,'SELECT id FROM ea.resource WHERE id=:id FOR UPDATE',id=v['resource_id'])
        prev=await b.one(db,'SELECT * FROM ea.publication_event WHERE resource_version_id=:v AND usergroup_id=:g ORDER BY event_no DESC LIMIT 1',v=resource_version_id,g=data['usergroup_id'])
        if data['expected_event_no']!=(prev['event_no'] if prev else 0) or str(data['previous_event_id'] or '')!=str(prev['id'] if prev else ''):fail('VERSION_CONFLICT',409,'发布状态已变化')
        if data['action']=='publish':
            if v['source_manifest'].get('artifact_sha256'):
                if v['provenance']=='simulated' and os.getenv('EA_GENERATION_FIXTURE_COURSE_ID') != str(v['course_id']):
                    fail('FIXTURE_COURSE_DISABLED',409,'该课程未启用合成联调')
                latest = b.must(await b.one(db,'''SELECT id FROM ea.resource_version
                    WHERE resource_id=:id ORDER BY version DESC LIMIT 1''',id=v['resource_id']))
                if latest['id'] != v['id']:
                    fail('VERSION_CONFLICT',409,'请审核并发布当前候选版本')
            check=b.must(await b.one(db,"SELECT checks FROM ea.content_check WHERE id=:id AND resource_version_id=:v AND policy_version_id=:policy AND check_kind='deterministic' AND verdict='pass'",id=data['validation_id'],v=v['id'],policy=data['policy_version_id']))
            if not required_checks_pass(rules,check['checks']) or not required_checks_pass(rules,await deterministic_checks(db,actor,v)):fail('POLICY_CHECKS_REQUIRED',409,'当前策略要求的检查尚未通过')
            requires_review=v['forced_review'] or rules['require_human_review'] or (v['generation_mode']=='rule_template' and not rules['allow_rule_auto_publish'])
            if requires_review:
                if not data['review_id']:fail('HUMAN_REVIEW_REQUIRED',409,'当前策略或资源强制要求教师审核')
                review=await b.one(db,"SELECT id FROM ea.content_check WHERE id=:id AND resource_version_id=:v AND policy_version_id=:policy AND check_kind='human_review' AND verdict='approve'",id=data['review_id'],v=v['id'],policy=data['policy_version_id'])
                if not review:fail('HUMAN_REVIEW_REQUIRED',409)
        row=b.must(await b.one(db,'INSERT INTO ea.publication_event(org_id,course_id,resource_version_id,usergroup_id,event_no,previous_event_id,action,validation_id,review_id,policy_version_id,actor_id,reason) VALUES(:org,:course,:v,:g,:number,:previous,:action,:validation,:review,:policy,:actor,:reason) RETURNING id',org=actor.org_id,course=v['course_id'],v=resource_version_id,g=data['usergroup_id'],number=data['expected_event_no']+1,previous=data['previous_event_id'],action=data['action'],validation=data['validation_id'],review=data['review_id'],policy=data['policy_version_id'],actor=actor.principal_id,reason=data['reason']))
        return {'publication_event_id':str(row['id'])}
    refs=await b.write_once(db,actor,request,'content.publish',data,v['course_id'],change)
    result=b.must(await b.one(db,'SELECT id,resource_version_id,usergroup_id,event_no,previous_event_id,action,policy_version_id,created_at FROM ea.publication_event WHERE id=:id',id=refs['publication_event_id']))
    result['etag']=b.etag(v['version'])
    return b.clean(result)


@router.get('/courses/{course_id}/bindings')
@safe
async def bindings(course_id:int,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    return await b.list_bindings(db,actor,course_id)


@router.get('/bindings/{binding_id}')
@safe
async def binding(binding_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    return await b.get_binding(db,actor,binding_id)


@router.post('/courses/{course_id}/bindings')
@safe
async def create_binding(course_id:int,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'BindingCreate');c=await b.course_row(db,actor,course_id,'task.manage',class_id=data['usergroup_id'])
    v=await b.resource_version(db,actor,data['resource_version_id'],'content.read')
    if v['course_id']!=course_id or not v['content'].get('questions'):fail('INVALID_ASSIGNMENT_RESOURCE',422,'请选择含题目的练习版本')
    await policy(db,actor,course_id,data['policy_version_id'])
    if (data['new_rubric'] is None)==(data['rubric_version_id'] is None):fail('INVALID_RUBRIC',422)
    async def change():
        rules=await current_policy(db,actor,course_id,data['policy_version_id'])
        if data['allow_review'] and not rules['allow_review']:fail('POLICY_REVIEW_DISABLED',422,'当前课程策略不允许新作业申请复核')
        checks=await deterministic_checks(db,actor,v)
        if not required_checks_pass(rules,checks):fail('POLICY_CHECKS_REQUIRED',409,'练习未满足当前策略的必需检查')
        b.matching(request,b.course_etag(c));now=datetime.now(timezone.utc).isoformat()
        publication=b.must(await b.one(db,"SELECT * FROM ea.publication_event WHERE id=:id AND resource_version_id=:version AND usergroup_id=:grp AND course_id=:course AND org_id=:org",id=data['publication_event_id'],version=v['id'],grp=data['usergroup_id'],course=course_id,org=actor.org_id))
        if publication['action']!='publish':fail('PUBLICATION_REQUIRED',409)
        requires_review=v['forced_review'] or rules['require_human_review'] or (v['generation_mode']=='rule_template' and not rules['allow_rule_auto_publish'])
        if requires_review:
            review=await b.one(db,"SELECT id FROM ea.content_check WHERE id=:id AND resource_version_id=:v AND check_kind='human_review' AND verdict='approve'",id=publication['review_id'],v=v['id'])
            if not review:fail('HUMAN_REVIEW_REQUIRED',409,'请先审核并重新发布练习')
        if data['new_rubric']:
            rubric=data['new_rubric'];maximum=sum(q['maximum_score'] for q in v['content']['questions'])
            validate_rubric(v['content'],rubric)
            if rubric['maximum_score']!=maximum:fail('RUBRIC_SCALE_MISMATCH',422,'量规上限必须等于题目分值总和')
            rid=str(uuid4());await b.execute(db,'INSERT INTO ea.rubric_version(id,org_id,course_id,family_id,version,criteria,maximum_score,content_hash,created_by) VALUES(:id,:org,:course,:family,1,CAST(:criteria AS jsonb),:maximum,:hash,:actor)',id=rid,org=actor.org_id,course=course_id,family=str(uuid4()),criteria=b.canonical(rubric['criteria']),maximum=maximum,hash=b.digest(rubric),actor=actor.principal_id)
        else:
            r=b.must(await b.one(db,'SELECT id,maximum_score,criteria FROM ea.rubric_version WHERE id=:id AND org_id=:org AND course_id=:course',id=data['rubric_version_id'],org=actor.org_id,course=course_id));rid=str(r['id'])
            validate_rubric(v['content'],r)
            if r['maximum_score']!=sum(q['maximum_score'] for q in v['content']['questions']):fail('RUBRIC_SCALE_MISMATCH',422)
        if data['assignment_id'] is None:
            chapter=await b.one(db,'SELECT id FROM public.chapter WHERE org_id=:org AND course_id=:course ORDER BY id LIMIT 1',org=actor.org_id,course=course_id)
            if not chapter:
                obj=Chapter(name='课程练习',org_id=actor.org_id,course_id=course_id,chapter_uuid='chapter_'+str(uuid4()),creation_date=now,update_date=now,lock_type=LockType.RESTRICTED);db.add(obj);await db.flush();chapter={'id':obj.id}
            activity=Activity(name=data['title'],org_id=actor.org_id,course_id=course_id,activity_uuid='activity_'+str(uuid4()),activity_type=ActivityTypeEnum.TYPE_ASSIGNMENT,activity_sub_type=ActivitySubTypeEnum.SUBTYPE_ASSIGNMENT_ANY,content={},published=False,lock_type=ActivityLockType.RESTRICTED,creation_date=now,update_date=now);db.add(activity);await db.flush()
            assignment=Assignment(title=data['title'],description=data['description'],org_id=actor.org_id,course_id=course_id,chapter_id=chapter['id'],activity_id=activity.id,assignment_uuid='assignment_'+str(uuid4()),grading_type=GradingTypeEnum.NUMERIC,published=False,creation_date=now,update_date=now);db.add(assignment);await db.flush();aid=assignment.id
        else:
            assignment=b.must(await b.one(db,'SELECT id FROM public.assignment WHERE id=:id AND course_id=:course AND org_id=:org',id=data['assignment_id'],course=course_id,org=actor.org_id));aid=assignment['id']
        prior=await b.one(db,'SELECT coalesce(max(version),0) version FROM ea.assignment_binding WHERE assignment_id=:id',id=aid)
        binding_id=str(uuid4())
        await b.execute(db,'INSERT INTO ea.assignment_binding(id,org_id,course_id,assignment_id,version,resource_version_id,rubric_version_id,policy_version_id,publication_event_id,usergroup_id,opens_at,due_at,max_attempts,answer_release,allow_review,rules_snapshot,created_by) VALUES(:id,:org,:course,:assignment,:version,:resource,:rubric,:policy,:publication,:grp,:opens,:due,:max,:release,:review,CAST(:rules AS jsonb),:actor)',id=binding_id,org=actor.org_id,course=course_id,assignment=aid,version=prior['version']+1,resource=v['id'],rubric=rid,policy=data['policy_version_id'],publication=data['publication_event_id'],grp=data['usergroup_id'],opens=datetime.fromisoformat(data['opens_at'].replace('Z','+00:00')),due=datetime.fromisoformat(data['due_at'].replace('Z','+00:00')),max=data['max_attempts'],release=data['answer_release'],review=data['allow_review'],rules=b.canonical({'title':data['title'],'description':data['description'],'provenance':'demo','policy_rules':rules,'deterministic_checks':checks,'checker_version':'demo-policy-v2'}),actor=actor.principal_id)
        members=await b.rows(db,"SELECT m.user_id FROM public.usergroupuser m JOIN ea.principal p ON p.user_id=m.user_id AND p.org_id=m.org_id WHERE m.org_id=:org AND m.usergroup_id=:grp AND (m.expires_at IS NULL OR m.expires_at>now()) AND p.disabled_at IS NULL AND NOT EXISTS (SELECT 1 FROM ea.action_grant g WHERE g.principal_id=p.id AND g.revoked_at IS NULL AND g.starts_at<=now() AND (g.expires_at IS NULL OR g.expires_at>now()))",org=actor.org_id,grp=data['usergroup_id'])
        if not members:fail('NO_RECIPIENTS',409,'当前班级没有可布置的学生')
        for member in members:
            await b.execute(db,'INSERT INTO ea.assignment_recipient(org_id,course_id,binding_id,student_id,usergroup_id,membership_snapshot) VALUES(:org,:course,:binding,:student,:grp,CAST(:snapshot AS jsonb))',org=actor.org_id,course=course_id,binding=binding_id,student=member['user_id'],grp=data['usergroup_id'],snapshot=b.canonical({'source':'current_direct_membership','provenance':'demo'}))
        return {'binding_id':binding_id}
    refs=await b.write_once(db,actor,request,'binding.create',data,course_id,change)
    return await b.get_binding(db,actor,refs['binding_id'])


@router.patch('/bindings/{binding_id}')
@safe
async def change_binding(binding_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'BindingStateRequest');obj=await b.binding_row(db,actor,binding_id,'task.manage')
    async def change():
        current=b.must(await b.one(db,'SELECT * FROM ea.assignment_binding WHERE id=:id FOR UPDATE',id=binding_id));b.matching(request,b.binding_etag(current))
        if current['status']=='archived':fail('ARCHIVED_READ_ONLY',409,'归档为终态，不能重新开放')
        await b.execute(db,'UPDATE ea.assignment_binding SET status=:status,state_reason=:reason,state_changed_at=now() WHERE id=:id',status=data['status'],reason=data['reason'],id=binding_id)
        return {'binding_id':str(binding_id)}
    await b.write_once(db,actor,request,'binding.state',data,obj['course_id'],change)
    return await b.get_binding(db,actor,binding_id)


@router.post('/bindings/{binding_id}/attempts')
@safe
async def start_attempt(binding_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'AttemptStart');obj=await b.binding_row(db,actor,binding_id)
    if actor.role!='student':fail('SCOPE_DENIED',403)
    async def change():
        current=await b.binding_row(db,actor,binding_id);b.matching(request,b.binding_etag(current))
        rec=b.must(await b.one(db,'SELECT * FROM ea.assignment_recipient WHERE id=:id AND binding_id=:binding AND student_id=:student FOR UPDATE',id=data['recipient_id'],binding=binding_id,student=actor.user_id))
        opened=await b.one(db,"SELECT id FROM ea.attempt WHERE recipient_id=:id AND status='draft'",id=rec['id'])
        if opened:return {'attempt_id':str(opened['id'])}
        prior=await b.one(db,'SELECT coalesce(max(attempt_no),0) number FROM ea.attempt WHERE recipient_id=:id',id=rec['id']);gid=data.get('resubmission_grant_id');slot=None
        if gid:
            grant=b.must(await b.one(db,'SELECT * FROM ea.resubmission_grant WHERE id=:id AND recipient_id=:r FOR UPDATE',id=gid,r=rec['id']));slots=await b.one(db,'SELECT coalesce(max(grant_slot),0) number FROM ea.attempt WHERE resubmission_grant_id=:id',id=gid);slot=slots['number']+1
        aid=str(uuid4());await b.execute(db,"INSERT INTO ea.attempt(id,org_id,course_id,recipient_id,binding_id,student_id,attempt_no,resource_version_id,rubric_version_id,resubmission_grant_id,grant_slot,draft,start_key) VALUES(:id,:org,:course,:recipient,:binding,:student,:number,:resource,:rubric,:grant,:slot,'{\"answers\":[]}',:key)",id=aid,org=actor.org_id,course=obj['course_id'],recipient=rec['id'],binding=binding_id,student=actor.user_id,number=prior['number']+1,resource=obj['resource_version_id'],rubric=obj['rubric_version_id'],grant=gid,slot=slot,key=request.headers['idempotency-key'])
        return {'attempt_id':aid}
    refs=await b.write_once(db,actor,request,'attempt.start',data,obj['course_id'],change)
    return await b.get_attempt(db,actor,refs['attempt_id'])


@router.get('/attempts/{attempt_id}')
@safe
async def read_attempt(attempt_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    return await b.get_attempt(db,actor,attempt_id)


def validate_answers(content,draft,complete=False):
    questions={q['item_id']:q for q in content['questions']};answers=draft['answers'];ids=[a['item_id'] for a in answers]
    if len(ids)!=len(set(ids)) or not set(ids).issubset(questions):fail('INVALID_ANSWER_SET',422,'题号重复或不属于绑定版本')
    if complete and set(ids)!=set(questions):fail('INCOMPLETE_ANSWER_SET',422,'请完成全部题目后提交')
    for a in answers:
        q=questions[a['item_id']];value=a['value']
        if q['type']=='boolean' and not isinstance(value,bool):fail('INVALID_ANSWER_TYPE',422,'判断题必须提交布尔选择')
        if q['type'] in ('short_answer','code_text','single_choice') and (not isinstance(value,str) or len(value)>20000):fail('INVALID_ANSWER_TYPE',422)
        if q['type']=='single_choice' and value not in {c['key'] for c in q.get('choices',[])}:fail('INVALID_ANSWER_TYPE',422,'单选题答案必须是实际选项')
        if complete and isinstance(value,str) and not value.strip():fail('INCOMPLETE_ANSWER_SET',422)


@router.put('/attempts/{attempt_id}/draft')
@safe
async def save_draft(attempt_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'DraftPut');obj=await b.attempt_row(db,actor,attempt_id)
    async def change():
        a=await b.attempt_row(db,actor,attempt_id,True);b.matching(request,b.etag(a['draft_revision']))
        if a['status']!='draft' or data['expected_version']!=a['draft_revision']:fail('VERSION_CONFLICT',409)
        content=b.must(await b.one(db,'SELECT content FROM ea.resource_version WHERE id=:id',id=a['resource_version_id']))['content'];validate_answers(content,data['draft'])
        await b.execute(db,'UPDATE ea.attempt SET draft=CAST(:draft AS jsonb),draft_revision=draft_revision+1,updated_at=now() WHERE id=:id',draft=b.canonical(data['draft']),id=attempt_id)
        return {'attempt_id':str(attempt_id),'draft_revision':a['draft_revision']+1}
    await b.write_once(db,actor,request,'attempt.draft',data,obj['course_id'],change)
    return await b.get_attempt(db,actor,attempt_id)


@router.post('/attempts/{attempt_id}/submissions')
@safe
async def submit(attempt_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'SubmitRequest');obj=await b.attempt_row(db,actor,attempt_id)
    async def change():
        a=await b.attempt_row(db,actor,attempt_id,True);b.matching(request,b.etag(a['draft_revision']))
        if a['status']!='draft' or data['expected_version']!=a['draft_revision'] or data['answer_hash']!=b.digest(a['draft']):fail('VERSION_CONFLICT',409,'提交必须匹配已保存的最新草稿')
        binding=await b.binding_row(db,actor,a['binding_id']);validate_answers(binding['content'],a['draft'],True)
        sid=str(uuid4());await b.execute(db,"INSERT INTO ea.submission(id,org_id,course_id,attempt_id,student_id,idempotency_key,request_hash,answer_snapshot,provenance) VALUES(:id,:org,:course,:attempt,:student,:key,:hash,CAST(:answer AS jsonb),'demo')",id=sid,org=actor.org_id,course=a['course_id'],attempt=attempt_id,student=actor.user_id,key=request.headers['idempotency-key'],hash=b.digest(data),answer=b.canonical(a['draft']))
        questions=binding['content']['questions'];keys={x['item_id']:x for x in binding['restricted_content'].get('answers',[])};answers={x['item_id']:x['value'] for x in a['draft']['answers']}
        if all(q['type'] in ('boolean','single_choice') and q['item_id'] in keys for q in questions) and objective_keys_valid(questions,{item:key['value'] for item,key in keys.items()}):
            item_scores=[{'item_id':q['item_id'],'score':q['maximum_score'] if type(answers[q['item_id']]) is type(keys[q['item_id']]['value']) and answers[q['item_id']]==keys[q['item_id']]['value'] else 0,'maximum_score':q['maximum_score'],'feedback':'依据绑定版本的标准答案评分'} for q in questions]
            eid=str(uuid4());await b.execute(db,"INSERT INTO ea.evaluation(id,org_id,course_id,submission_id,student_id,rubric_version_id,policy_version_id,source,outcome,score,maximum_score,details,feedback,producer_version,actor_id) VALUES(:id,:org,:course,:s,:student,:rubric,:policy,'rule','scored',:score,:max,CAST(:details AS jsonb),CAST(:feedback AS jsonb),'boolean-choice-v1',:actor)",id=eid,org=actor.org_id,course=a['course_id'],s=sid,student=actor.user_id,rubric=a['rubric_version_id'],policy=binding['policy_version_id'],score=sum(x['score'] for x in item_scores),max=binding['maximum_score'],details=b.canonical({'item_scores':item_scores}),feedback=b.canonical({'text':'客观题已按固定答案完成评分，未运行模型或代码。'}),actor=actor.service_id)
            s=await b.submission_row(db,actor,sid);await b.add_grade(db,actor,s,eid,'客观题规则评分自动生效',0,None,True)
        return {'submission_id':sid}
    refs=await b.write_once(db,actor,request,'attempt.submit',data,obj['course_id'],change)
    return (await b.get_submission(db,actor,refs['submission_id']))['receipt']


@router.get('/bindings/{binding_id}/submissions')
@safe
async def submissions(binding_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    obj=await b.binding_row(db,actor,binding_id,'submission.grade')
    if actor.role!='teacher':fail('SCOPE_DENIED',403)
    result=[]
    for s in await b.rows(db,'SELECT s.id FROM ea.submission s JOIN ea.attempt a ON a.id=s.attempt_id WHERE a.binding_id=:id ORDER BY s.accepted_at DESC',id=binding_id):
        view=await b.get_submission(db,actor,s['id'])
        result.append({'submission_id':view['receipt']['submission_id'],'student_id':view['receipt']['student_id'],'accepted_at':view['receipt']['accepted_at'],'effective_grade':view['effective_grade'],'pending_review':any(r['status']=='open' for r in view['reviews'])})
    return {'items':result,'page':{'has_more':False,'next_cursor':None}}


@router.get('/submissions/{submission_id}')
@safe
async def submission(submission_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    return await b.get_submission(db,actor,submission_id)


@router.post('/submissions/{submission_id}/evaluations')
@safe
async def evaluate(submission_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'EvaluationRequest');s=await b.submission_row(db,actor,submission_id,'submission.grade')
    if actor.role!='teacher':fail('SCOPE_DENIED',403)
    if data['source']=='ai_suggestion':fail('MODEL_NOT_CONFIGURED',503,'AI批阅未配置，可直接进行教师评分')
    async def change():
        current=await b.submission_row(db,actor,submission_id,'submission.grade',True);history=await b.grades(db,submission_id);b.matching(request,b.etag(history[0]['revision'] if history else 0))
        rubric=b.must(await b.one(db,'SELECT maximum_score FROM ea.rubric_version WHERE id=:id',id=current['rubric_version_id']))
        content=b.must(await b.one(db,'SELECT content FROM ea.resource_version WHERE id=:id',id=current['resource_version_id']))['content'];questions={q['item_id']:q for q in content['questions']}
        if data['maximum_score']!=rubric['maximum_score'] or data['score']>data['maximum_score']:fail('RUBRIC_SCALE_MISMATCH',422)
        scores=data['item_scores'];ids=[x['item_id'] for x in scores]
        if set(ids)!=set(questions) or len(ids)!=len(set(ids)) or abs(sum(x['score'] for x in scores)-data['score'])>0.0001:fail('INVALID_ITEM_SCORES',422,'分项评分应覆盖所有题目且总分一致')
        for score in scores:
            if score['score']>questions[score['item_id']]['maximum_score']:fail('INVALID_ITEM_SCORES',422)
        eid=str(uuid4());details={'item_scores':[dict(x,maximum_score=questions[x['item_id']]['maximum_score']) for x in scores]}
        await b.execute(db,"INSERT INTO ea.evaluation(id,org_id,course_id,submission_id,student_id,rubric_version_id,policy_version_id,source,outcome,score,maximum_score,details,feedback,producer_version,actor_id) VALUES(:id,:org,:course,:submission,:student,:rubric,:policy,'teacher','scored',:score,:max,CAST(:details AS jsonb),CAST(:feedback AS jsonb),'teacher-confirmation-v1',:actor)",id=eid,org=actor.org_id,course=s['course_id'],submission=submission_id,student=s['student_id'],rubric=s['rubric_version_id'],policy=s['policy_version_id'],score=data['score'],max=data['maximum_score'],details=b.canonical(details),feedback=b.canonical({'text':data['feedback'],'reason':data['reason']}),actor=actor.principal_id)
        return {'evaluation_id':eid}
    refs=await b.write_once(db,actor,request,'submission.evaluate',data,s['course_id'],change)
    return b.clean(await b.one(db,"SELECT id,submission_id,source,outcome,score,maximum_score,details->'item_scores' item_scores,feedback->>'text' feedback,producer_version,created_at FROM ea.evaluation WHERE id=:id",id=refs['evaluation_id']))


@router.post('/submissions/{submission_id}/grade-revisions')
@safe
async def apply_grade(submission_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'GradeCreate');s=await b.submission_row(db,actor,submission_id,'submission.grade')
    if actor.role!='teacher':fail('SCOPE_DENIED',403)
    async def change():
        current=await b.submission_row(db,actor,submission_id,'submission.grade',True);history=await b.grades(db,submission_id);b.matching(request,b.etag(history[0]['revision'] if history else 0))
        g=await b.add_grade(db,actor,current,data['evaluation_id'],data['reason'],data['expected_version'],data['supersedes_id']);return {'grade_revision_id':str(g['id'])}
    refs=await b.write_once(db,actor,request,'grade.apply',data,s['course_id'],change)
    return b.clean(next(g for g in await b.grades(db,submission_id) if str(g['id'])==refs['grade_revision_id']))


@router.post('/submissions/{submission_id}/reviews')
@safe
async def request_review(submission_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'ReviewCreate');s=await b.submission_row(db,actor,submission_id)
    if actor.role!='student' or not s['allow_review']:fail('SCOPE_DENIED',403)
    async def change():
        current=await b.submission_row(db,actor,submission_id,lock=True);history=await b.grades(db,submission_id)
        if not history:fail('NO_EFFECTIVE_GRADE',409)
        b.matching(request,b.etag(history[0]['revision']))
        if str(history[0]['id'])!=data['grade_revision_id']:fail('VERSION_CONFLICT',409)
        row=b.must(await b.one(db,'INSERT INTO ea.review_request(org_id,course_id,submission_id,student_id,grade_revision_id,request_text) VALUES(:org,:course,:submission,:student,:grade,:reason) RETURNING id',org=actor.org_id,course=s['course_id'],submission=submission_id,student=actor.user_id,grade=data['grade_revision_id'],reason=data['request_text']))
        return {'review_request_id':str(row['id'])}
    refs=await b.write_once(db,actor,request,'review.request',data,s['course_id'],change)
    return await b.review_view(db,refs['review_request_id'])


@router.post('/reviews/{review_request_id}/resolution')
@safe
async def resolve_review(review_request_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'ReviewResolution');review=b.must(await b.one(db,'SELECT * FROM ea.review_request WHERE id=:id AND org_id=:org',id=review_request_id,org=actor.org_id));s=await b.submission_row(db,actor,review['submission_id'],'result.review')
    if actor.role!='teacher':fail('SCOPE_DENIED',403)
    async def change():
        current=await b.submission_row(db,actor,s['id'],'result.review',True);r=b.must(await b.one(db,'SELECT * FROM ea.review_request WHERE id=:id FOR UPDATE',id=review_request_id));history=await b.grades(db,s['id']);b.matching(request,b.etag(history[0]['revision']))
        if r['status']!='open' or history[0]['revision']!=data['expected_grade_revision'] or history[0]['id']!=r['grade_revision_id']:fail('VERSION_CONFLICT',409)
        revised=None
        if data['decision']=='revise':
            if not data['evaluation_id']:fail('EVALUATION_REQUIRED',422)
            g=await b.add_grade(db,actor,current,data['evaluation_id'],data['reason'],history[0]['revision'],history[0]['id']);revised=g['id']
        elif data['evaluation_id'] is not None:fail('INVALID_REQUEST',422)
        await b.execute(db,'UPDATE ea.review_request SET status=:status,resolved_revision_id=:grade,resolution=:reason,resolved_by=:actor,resolved_at=now() WHERE id=:id',status='revised' if revised else 'retained',grade=revised,reason=data['reason'],actor=actor.principal_id,id=review_request_id)
        return {'review_request_id':str(review_request_id)}
    refs=await b.write_once(db,actor,request,'review.resolve',data,s['course_id'],change)
    return await b.review_view(db,refs['review_request_id'])


@router.post('/recipients/{recipient_id}/resubmission-grants')
@safe
async def resubmission_grant(recipient_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'ResubmissionGrantCreate');rec=b.must(await b.one(db,'SELECT * FROM ea.assignment_recipient WHERE id=:id AND org_id=:org',id=recipient_id,org=actor.org_id));obj=await b.binding_row(db,actor,rec['binding_id'],'task.manage')
    await require_course(db,actor,obj['course_id'],action='task.manage',student_id=rec['student_id'],class_id=rec['usergroup_id'])
    async def change():
        b.matching(request,b.binding_etag(obj));row=b.must(await b.one(db,'INSERT INTO ea.resubmission_grant(org_id,course_id,recipient_id,binding_id,student_id,grant_kind,starts_at,expires_at,allowed_attempts,granted_by,reason) VALUES(:org,:course,:recipient,:binding,:student,:kind,:starts,:expires,:allowed,:actor,:reason) RETURNING id',org=actor.org_id,course=obj['course_id'],recipient=recipient_id,binding=rec['binding_id'],student=rec['student_id'],kind=data['grant_kind'],starts=datetime.fromisoformat(data['starts_at'].replace('Z','+00:00')),expires=datetime.fromisoformat(data['expires_at'].replace('Z','+00:00')),allowed=data['allowed_attempts'],actor=actor.principal_id,reason=data['reason']))
        return {'grant_id':str(row['id'])}
    refs=await b.write_once(db,actor,request,'resubmission.grant',data,obj['course_id'],change)
    return b.clean(await b.one(db,'SELECT * FROM ea.resubmission_grant WHERE id=:id',id=refs['grant_id']))


@router.get('/courses/{course_id}/students/{student_id}/learning-state')
@safe
async def state(course_id:int,student_id:int,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    return await b.learning_state(db,actor,course_id,student_id)


@router.get('/courses/{course_id}/students/{student_id}/recommendations')
@safe
async def recommend(course_id:int,student_id:int,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    if actor.role == 'student':
        decision = await ri.read_decision(db, actor, course_id, student_id)
        if decision is not None:
            return decision
    return await b.recommendations(db,actor,course_id,student_id)


@router.post('/courses/{course_id}/students/{student_id}/recommendations/jobs')
@safe
async def register_recommendation(course_id:int, student_id:int, profile_version_id:UUID,
                                  request:Request, actor=Depends(require_actor), db=Depends(get_db_session)):
    return await ri.register(db, actor, course_id, student_id, profile_version_id, request)


@router.post('/courses/{course_id}/students/{student_id}/recommendations/jobs/{task_id}/import')
@safe
async def import_recommendation(course_id:int, student_id:int, task_id:UUID,
                                actor=Depends(require_actor), db=Depends(get_db_session)):
    return await ri.import_result(db, actor, course_id, student_id, task_id)


@router.get('/courses/{course_id}/students/{student_id}/recommendations/{version_id}/exercises/{binding_id}')
@safe
async def open_decision_exercise(course_id:int, student_id:int, version_id:UUID, binding_id:UUID,
                                 actor=Depends(require_actor), db=Depends(get_db_session)):
    view = await ri.read_decision(db, actor, course_id, student_id, version_id)
    if view['state'] == 'stale':
        fail('STALE_EVIDENCE', 409)
    item = next((i for i in view['items'] if i['binding_id'] == str(binding_id)), None)
    if item is None:
        fail('EXERCISE_UNAVAILABLE', 404)
    return {**await b.get_binding(db, actor, binding_id), 'start_option': item['start_option']}


@router.get('/courses/{course_id}/recommendation-exercises')
@safe
async def exercise_matches(course_id: int, concept_id: list[str] = Query(...),
                           profile_version_id: str | None = None,
                           actor=Depends(require_actor), db=Depends(get_db_session)):
    return await resolve_exercises(db, actor, course_id, concept_id, profile_version_id=profile_version_id)


@router.get('/courses/{course_id}/recommendation-exercises/{binding_id}')
@safe
async def open_exercise_match(course_id: int, binding_id: UUID, resource_version_id: UUID,
                              publication_event_id: UUID, concept_id: list[str] = Query(...),
                              profile_version_id: str | None = None,
                              actor=Depends(require_actor), db=Depends(get_db_session)):
    matches = await resolve_exercises(db, actor, course_id, concept_id, binding_id=binding_id,
                                      profile_version_id=profile_version_id)
    if not matches['items']:
        fail('EXERCISE_UNAVAILABLE', 404)
    item = matches['items'][0]
    if (item['resource_version_id'] != str(resource_version_id)
            or item['publication_event_id'] != str(publication_event_id)):
        fail('EXERCISE_VERSION_CHANGED', 409)
    return {**await b.get_binding(db, actor, binding_id), 'start_option': item['start_option']}


@router.get('/evidence/{evidence_id}/content')
@safe
async def evidence(evidence_id:UUID,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    e=b.must(await b.one(db,'SELECT * FROM ea.evidence_event WHERE id=:id AND org_id=:org',id=evidence_id,org=actor.org_id))
    if actor.role=='student' and actor.user_id!=e['student_id']:fail('SCOPE_DENIED',403)
    await require_course(db,actor,e['course_id'],action='evidence.read' if actor.role=='teacher' else None,student_id=e['student_id'])
    if e['source_type']!='effective_grade':fail('CAPABILITY_NOT_IMPLEMENTED',409,'本版此入口只接有效成绩证据')
    g=b.must(await b.one(db,'SELECT submission_id FROM ea.grade_revision WHERE id=:id',id=e['grade_revision_id']))
    submission=await b.submission_row(db,actor,g['submission_id'],'evidence.read' if actor.role=='teacher' else None)
    version=b.must(await b.one(db,'SELECT content FROM ea.resource_version WHERE id=:id',id=submission['resource_version_id']))
    grades=await b.grades(db,submission['id'])
    return b.clean({'evidence_id':str(evidence_id),'source_type':'effective_grade','submission_id':str(g['submission_id']),'question_content':version['content'],'answer_snapshot':submission['answer_snapshot'],'effective_grade':grades[0] if grades else None})


@router.post('/courses/{course_id}/students/{student_id}/interventions')
@safe
async def intervention(course_id:int,student_id:int,request:Request,actor=Depends(require_actor),db=Depends(get_db_session)):
    data=await payload(request,'InterventionCreate');await require_course(db,actor,course_id,action='intervention.write',student_id=student_id)
    if actor.role!='teacher':fail('SCOPE_DENIED',403)
    if data['action']!='record_support':fail('INTERVENTION_ASSIGNMENT_NOT_CONNECTED',409,'本版仅支持记录教学支持；布置新练习请使用课程作业流程')
    async def change():
        p=b.must(await b.one(db,'SELECT id,version FROM ea.profile_version WHERE org_id=:org AND course_id=:course AND student_id=:student ORDER BY version DESC LIMIT 1',org=actor.org_id,course=course_id,student=student_id));b.matching(request,b.etag(p['version']))
        if str(p['id'])!=data['profile_version_id']:fail('VERSION_CONFLICT',409)
        if data.get('target_binding_id'):
            target=await b.binding_row(db,actor,data['target_binding_id'],'task.manage')
            if target['course_id']!=course_id:fail('SCOPE_DENIED',403)
            if not await b.one(db,'SELECT id FROM ea.assignment_recipient WHERE binding_id=:binding AND student_id=:student',binding=data['target_binding_id'],student=student_id):fail('SCOPE_DENIED',403)
        row=b.must(await b.one(db,'INSERT INTO ea.intervention_event(org_id,course_id,student_id,profile_version_id,recommendation_version_id,target_binding_id,action,reason,actor_id,details) VALUES(:org,:course,:student,:profile,:rec,:binding,:action,:reason,:actor,CAST(:details AS jsonb)) RETURNING id',org=actor.org_id,course=course_id,student=student_id,profile=data['profile_version_id'],rec=data.get('recommendation_version_id'),binding=data.get('target_binding_id'),action=data['action'],reason=data['reason'],actor=actor.principal_id,details=b.canonical(data.get('details',{}))))
        return {'intervention_id':str(row['id'])}
    refs=await b.write_once(db,actor,request,'intervention.record',data,course_id,change)
    return b.clean(await b.one(db,'SELECT * FROM ea.intervention_event WHERE id=:id',id=refs['intervention_id']))
