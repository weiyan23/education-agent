"""Education demo business services, backed by immutable ea PostgreSQL facts.

Only deterministic rules run here. No model requests or student code execution.
Every query binds values; table names below are literals controlled by this module.
"""
from __future__ import annotations
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import text
from src.education.security import require_course, allowed_class_ids, fail

SCHEMAS = json.loads(Path(__file__).with_name('business_schema.json').read_text())
CHECKER = FormatChecker()


def canonical(value):
    return json.dumps(jsonable_encoder(value),ensure_ascii=False,sort_keys=True,separators=(',',':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def clean(value):
    return jsonable_encoder(value)


def generation_env_file():
    configured = os.getenv('EA_GENERATION_ENV_FILE')
    if configured:
        path = Path(configured).expanduser()
        if path.is_absolute() and path.is_file():
            return path


def etag(value):
    return '"' + str(value) + '"'


def validate(name, data):
    validator = Draft202012Validator(dict(SCHEMAS, **{'$ref':'#/components/schemas/'+name}), format_checker=CHECKER)
    if not validator.is_valid(data):
        fail('INVALID_REQUEST',422,'请求字段或格式不符合接口约定')
    return data


def policy_rules(policy):
    rules=policy['rules']
    validator=Draft202012Validator(dict(SCHEMAS, **{'$ref':'#/components/schemas/PolicyRules'}),format_checker=CHECKER)
    if validator.is_valid(rules):return rules
    # Historical seed policies remain immutable; they never gain permissive defaults.
    return {'allowed_generation_modes':['rule_template'],'required_checks':['schema','source_ready','scope','answer_consistency','rubric_bounds','boolean_truth_table'],'require_human_review':True,'allow_rule_auto_publish':False,'maximum_task_budget':0,'maximum_task_attempts':1,'allow_review':False}


async def rows(db, sql, **values):
    result = await db.execute(text(sql),values)
    return [dict(r) for r in result.mappings().all()]


async def one(db, sql, **values):
    result = await rows(db,sql,**values)
    return result[0] if result else None


async def execute(db, sql, **values):
    return await db.execute(text(sql),values)


def must(row):
    if not row:fail('NOT_FOUND',404,'对象不存在或不可访问')
    return row


def matching(request, actual):
    supplied=request.headers.get('if-match')
    if supplied is None:fail('PRECONDITION_REQUIRED',428,'请刷新页面后重试')
    if supplied != actual:fail('VERSION_CONFLICT',409,'内容已更新，请刷新后重试')


async def write_once(db,actor,request,operation,data,course_id,change):
    key=request.headers.get('idempotency-key','')
    if not 1<=len(key)<=120:fail('IDEMPOTENCY_KEY_REQUIRED',400,'缺少合法的请求幂等键')
    request_hash=digest({'path':request.url.path,'body':data,'if_match':request.headers.get('if-match')})
    identity=str(actor.principal_id)+':'+operation+':'+key
    await execute(db,'SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))',identity=str(actor.org_id)+':'+identity)
    old=await one(db,"SELECT payload FROM ea.outbox_event WHERE org_id=:org AND event_type='command.receipt' AND deduplication_key=:key",org=actor.org_id,key=identity)
    if old:
        if old['payload']['request_hash']!=request_hash:fail('IDEMPOTENCY_CONFLICT',409,'同一请求标识不能用于不同内容')
        return old['payload']['result_refs']
    result=clean(await change())
    # The command functions return IDs/versions only, never answer text or secrets.
    await execute(db,"INSERT INTO ea.outbox_event(org_id,course_id,event_type,aggregate_type,aggregate_id,deduplication_key,payload,published_at) VALUES(:org,:course,'command.receipt','command',:id,:key,CAST(:payload AS jsonb),now())",org=actor.org_id,course=course_id,id=str(uuid4()),key=identity,payload=canonical({'request_hash':request_hash,'result_refs':result}))
    await execute(db,"INSERT INTO ea.audit_event(org_id,course_id,actor_id,action,target_type,target_id,request_id,reason_code,metadata) VALUES(:org,:course,:actor,:action,'command',:target,:request,'USER_COMMAND','{}')",org=actor.org_id,course=course_id,actor=actor.principal_id,action=operation,target=request.url.path,request=str(uuid4()))
    await db.commit()
    return result


async def course_row(db,actor,course_id,action=None,student_id=None,class_id=None):
    await require_course(db,actor,course_id,action=action,student_id=student_id,class_id=class_id)
    return must(await one(db,'SELECT id,org_id,course_uuid,name,description,learnings,published,update_date FROM public.course WHERE id=:id AND org_id=:org',id=course_id,org=actor.org_id))


def course_etag(row):return etag(digest({'id':row['id'],'updated':row['update_date']})[:24])


async def course_summary(db,actor,c):
    classes=await allowed_class_ids(db,actor,c['id'])
    grants=await rows(db,"SELECT DISTINCT action FROM ea.action_grant WHERE org_id=:org AND principal_id=:actor AND course_id=:course AND revoked_at IS NULL AND starts_at<=now() AND (expires_at IS NULL OR expires_at>now())",org=actor.org_id,actor=actor.principal_id,course=c['id']) if actor.role=='teacher' else []
    return {'id':c['id'],'org_id':c['org_id'],'course_uuid':c['course_uuid'],'name':c['name'],'published':c['published'],'class_ids':classes,'allowed_actions':[g['action'] for g in grants],'etag':course_etag(c)}


async def courses(db,actor):
    if actor.role=='admin':return {'items':[],'page':{'has_more':False,'next_cursor':None}}
    result=[]
    for c in await rows(db,'SELECT id,org_id,course_uuid,name,published,update_date FROM public.course WHERE org_id=:org ORDER BY id',org=actor.org_id):
        try:
            await require_course(db,actor,c['id'])
        except HTTPException as e:
            if e.status_code==403:continue
            raise
        result.append(await course_summary(db,actor,c))
    return {'items':result,'page':{'has_more':False,'next_cursor':None}}


async def resource_version(db,actor,version_id,action=None):
    v=must(await one(db,'SELECT v.*,r.title,r.kind,r.archived_at FROM ea.resource_version v JOIN ea.resource r ON r.id=v.resource_id WHERE v.id=:id AND v.org_id=:org',id=version_id,org=actor.org_id))
    await require_course(db,actor,v['course_id'],action=action)
    if actor.role=='student':
        class_ids=await allowed_class_ids(db,actor,v['course_id'])
        events=await rows(db,'SELECT DISTINCT ON(usergroup_id) usergroup_id,action FROM ea.publication_event WHERE resource_version_id=:id ORDER BY usergroup_id,event_no DESC',id=version_id)
        if v['archived_at'] or not any(e['action']=='publish' and e['usergroup_id'] in class_ids for e in events):fail('SCOPE_DENIED',403,'资料尚未向你发布')
    return v


async def version_summary(db,v,class_ids=None):
    checks=await rows(db,'SELECT id,check_kind,verdict,created_at FROM ea.content_check WHERE resource_version_id=:id ORDER BY created_at DESC',id=v['id'])
    events=await rows(db,'SELECT DISTINCT ON(usergroup_id) id,usergroup_id,action,event_no,created_at FROM ea.publication_event WHERE resource_version_id=:id ORDER BY usergroup_id,event_no DESC',id=v['id'])
    if class_ids is not None:events=[e for e in events if e['usergroup_id'] in class_ids]
    summary={k:v[k] for k in ['id','resource_id','course_id','version','processing_state','generation_mode','provenance','forced_review','content_hash','policy_version_id']}
    summary.update(title=v['title'],kind=v['kind'],validation_verdict=next((c['verdict'] for c in checks if c['check_kind']=='deterministic'),None),review_verdict=next((c['verdict'] for c in checks if c['check_kind']=='human_review'),None),publication_action='publish' if any(e['action']=='publish' for e in events) else ('withdraw' if events else None),etag=etag(v['version']))
    return clean(summary),clean(checks),clean(events)


async def get_resource(db,actor,resource_id):
    r=must(await one(db,'SELECT * FROM ea.resource WHERE id=:id AND org_id=:org',id=resource_id,org=actor.org_id))
    await require_course(db,actor,r['course_id'])
    versions=await rows(db,'SELECT v.*,r.title,r.kind,r.archived_at FROM ea.resource_version v JOIN ea.resource r ON r.id=v.resource_id WHERE resource_id=:id ORDER BY version DESC',id=resource_id)
    selected=None
    for v in versions:
        try:selected=await resource_version(db,actor,v['id']);break
        except HTTPException as e:
            if e.status_code==403 and actor.role=='student':continue
            raise
    if selected is None:fail('SCOPE_DENIED',403,'没有可访问的已发布版本')
    classes=await allowed_class_ids(db,actor,r['course_id'])
    summary,checks,events=await version_summary(db,selected,classes)
    result={'resource_id':r['id'],'course_id':r['course_id'],'title':r['title'],'kind':r['kind'],'version':summary,'content':selected['content'],'etag':etag(selected['version'])}
    if actor.role=='teacher':
        await require_course(db,actor,r['course_id'],action='content.read')
        result.update(restricted_content=selected['restricted_content'],checks=checks,publication_events=events,version_history=[(await version_summary(db,v,classes))[0] for v in versions])
        if selected['source_manifest'].get('artifact_sha256'):
            result['candidate_history'] = [
                {'version': v['version'], 'content': v['content'],
                 'grading_notes': (v['restricted_content'] or {}).get('grading_notes', '')}
                for v in versions if v['id'] != selected['id']
            ]
    return clean(result)


async def get_course(db,actor,course_id):
    c=await course_row(db,actor,course_id)
    ids=await allowed_class_ids(db,actor,course_id)
    groups=await rows(db,'SELECT id,name FROM public.usergroup WHERE org_id=:org ORDER BY id',org=actor.org_id)
    groups=[g for g in groups if g['id'] in ids]
    for g in groups:
        if actor.role=='teacher':
            members=await rows(db,'SELECT u.id user_id,u.username display_name FROM public.usergroupuser m JOIN public."user" u ON u.id=m.user_id JOIN ea.principal p ON p.user_id=u.id AND p.org_id=m.org_id WHERE m.org_id=:org AND m.usergroup_id=:grp AND p.disabled_at IS NULL AND (m.expires_at IS NULL OR m.expires_at>now()) ORDER BY u.id',org=actor.org_id,grp=g['id'])
            visible=[]
            for m in members:
                try:await require_course(db,actor,course_id,student_id=m['user_id'],class_id=g['id'])
                except HTTPException:continue
                visible.append(m)
            g['members']=visible
        else:g['members']=[]
    resources=[]
    for r in await rows(db,'SELECT id FROM ea.resource WHERE course_id=:course AND org_id=:org AND archived_at IS NULL ORDER BY created_at DESC',course=course_id,org=actor.org_id):
        try:resources.append((await get_resource(db,actor,r['id']))['version'])
        except HTTPException as e:
            if e.status_code==403:continue
            raise
    policy=await one(db,"SELECT id,rules FROM ea.policy_version WHERE course_id=:course AND org_id=:org ORDER BY version DESC,created_at DESC LIMIT 1",course=course_id,org=actor.org_id)
    bindings=(await list_bindings(db,actor,course_id))['items']
    summary = await course_summary(db,actor,c)
    capabilities = [
        {'key':'course_qa','status':'unconfigured','reason':'尚未配置模型，资料阅读与笔记可用'},
        {'key':'research_profile','status':'unconfigured','reason':'研究画像未执行，当前展示有效成绩统计'},
    ]
    if actor.role=='teacher' and 'content.generate' in summary['allowed_actions'] and os.getenv('EA_GENERATION_FIXTURE_COURSE_ID') == str(course_id):
        capabilities.append({'key':'generation_fixture','status':'available','reason':'合成 Java 出题联调'})
    if actor.role=='teacher' and 'content.generate' in summary['allowed_actions'] and generation_env_file() and policy:
        rules = policy_rules(policy)
        if 'research_generator' in rules['allowed_generation_modes'] and rules['maximum_task_budget'] > 0:
            catalog = Path(__file__).resolve().parents[8] / 'config/java-basics.json'
            codes = list(json.loads(catalog.read_text(encoding='utf-8'))['concepts'])
            capabilities.append({'key':'generation_execute','status':'available','reason':'模型出题',
                                 'concept_codes':codes,'maximum_task_budget':rules['maximum_task_budget']})
    return clean({'course':summary,'description':c['description'] or '', 'learnings':c['learnings'] or '', 'chapters':[],'classes':groups,'resources':resources,'bindings':bindings,'policy_version_id':policy['id'] if policy else None,'knowledge_points':await rows(db,'SELECT id,code,label,version FROM ea.knowledge_point WHERE course_id=:course AND org_id=:org ORDER BY code',course=course_id,org=actor.org_id),'capabilities':capabilities,'etag':course_etag(c)})


async def binding_row(db,actor,binding_id,action=None):
    b=must(await one(db,"SELECT b.*,COALESCE(b.rules_snapshot->>'title',a.title) title,COALESCE(b.rules_snapshot->>'description',a.description) description,r.criteria,r.maximum_score,v.content,v.restricted_content FROM ea.assignment_binding b JOIN public.assignment a ON a.id=b.assignment_id JOIN ea.rubric_version r ON r.id=b.rubric_version_id JOIN ea.resource_version v ON v.id=b.resource_version_id WHERE b.id=:id AND b.org_id=:org",id=binding_id,org=actor.org_id))
    await require_course(db,actor,b['course_id'],action=action,class_id=b['usergroup_id'],student_id=actor.user_id if actor.role=='student' else None)
    if actor.role=='student':
        rec=await one(db,'SELECT id FROM ea.assignment_recipient WHERE binding_id=:b AND student_id=:u',b=binding_id,u=actor.user_id)
        if not rec:fail('SCOPE_DENIED',403,'此作业未布置给你')
    return b


def binding_etag(b):return etag(digest({'id':b['id'],'state':b['status'],'time':b['state_changed_at']})[:24])


async def list_bindings(db,actor,course_id):
    await course_row(db,actor,course_id)
    items=[]
    for b in await rows(db,'SELECT id FROM ea.assignment_binding WHERE org_id=:org AND course_id=:course ORDER BY created_at DESC',org=actor.org_id,course=course_id):
        try:view=await get_binding(db,actor,b['id'])
        except HTTPException as e:
            if e.status_code==403:continue
            raise
        items.append(view)
    return {'items':items,'page':{'has_more':False,'next_cursor':None}}


async def get_binding(db,actor,binding_id):
    b=await binding_row(db,actor,binding_id)
    result={k:b[k] for k in ['id','assignment_id','org_id','course_id','version','title','description','resource_version_id','rubric_version_id','policy_version_id','publication_event_id','usergroup_id','opens_at','due_at','max_attempts','answer_release','allow_review','status']}
    result.update(binding_id=b['id'],recipient=None,own_submission_ids=[],grading_criteria=b['criteria'],maximum_score=b['maximum_score'],question_content=b['content'],etag=binding_etag(b))
    if actor.role=='student':
        rec=must(await one(db,'SELECT id,binding_id,student_id,usergroup_id FROM ea.assignment_recipient WHERE binding_id=:id AND student_id=:u',id=binding_id,u=actor.user_id))
        rec['active_grants']=await rows(db,'SELECT id,grant_kind,starts_at,expires_at,allowed_attempts FROM ea.resubmission_grant WHERE recipient_id=:id AND revoked_at IS NULL AND starts_at<=now() AND expires_at>now()',id=rec['id'])
        rec['attempts']=await rows(db,'SELECT id,attempt_no,status,draft_revision FROM ea.attempt WHERE recipient_id=:id ORDER BY attempt_no',id=rec['id'])
        result['recipient']=rec
        # A withdrawn publication cannot reveal new assignment content. Existing
        # attempts retain their immutable question snapshot for historical use.
        if not rec['attempts']:
            try:await resource_version(db,actor,b['resource_version_id'])
            except HTTPException as exc:
                if exc.status_code!=403:raise
                result['question_content']={}
                result['content_access']='withdrawn'
        sub=await rows(db,'SELECT s.id FROM ea.submission s JOIN ea.attempt a ON a.id=s.attempt_id WHERE a.binding_id=:b AND s.student_id=:u ORDER BY accepted_at',b=binding_id,u=actor.user_id)
        result['own_submission_ids']=[s['id'] for s in sub]
        if result.get('content_access')=='withdrawn':pass
        elif b['answer_release']=='after_due' and datetime.now(timezone.utc)>=b['due_at']:
            result['answer_key']=b['restricted_content']
        elif b['answer_release']=='after_effective' and sub:
            effective=await one(db,'SELECT g.id FROM ea.grade_revision g JOIN ea.submission s ON s.id=g.submission_id JOIN ea.attempt a ON a.id=s.attempt_id WHERE a.binding_id=:b AND s.student_id=:u LIMIT 1',b=binding_id,u=actor.user_id)
            if effective:result['answer_key']=b['restricted_content']
    elif actor.role=='teacher':
        await require_course(db,actor,b['course_id'],action='task.manage',class_id=b['usergroup_id'])
        result['recipients']=await rows(db,'SELECT r.id,r.binding_id,r.student_id,r.usergroup_id,u.username display_name FROM ea.assignment_recipient r JOIN public."user" u ON u.id=r.student_id WHERE r.binding_id=:b ORDER BY r.student_id',b=binding_id)
        result['answer_key']=b['restricted_content']
    return clean(result)


async def attempt_row(db,actor,attempt_id,lock=False):
    a=must(await one(db,'SELECT * FROM ea.attempt WHERE id=:id AND org_id=:org'+(' FOR UPDATE' if lock else ''),id=attempt_id,org=actor.org_id))
    if actor.role!='student' or a['student_id']!=actor.user_id:fail('SCOPE_DENIED',403,'草稿仅本人可见')
    await binding_row(db,actor,a['binding_id'])
    return a


async def get_attempt(db,actor,attempt_id):
    a=await attempt_row(db,actor,attempt_id)
    v=must(await one(db,'SELECT content FROM ea.resource_version WHERE id=:id',id=a['resource_version_id']))
    result={k:a[k] for k in ['id','binding_id','recipient_id','student_id','attempt_no','resource_version_id','rubric_version_id','status','draft','draft_revision','resubmission_grant_id','updated_at']}
    result.update(question_content=v['content'],draft_hash=digest(a['draft']),etag=etag(a['draft_revision']))
    return clean(result)


async def submission_row(db,actor,submission_id,action=None,lock=False):
    s=must(await one(db,'SELECT s.*,a.binding_id,a.recipient_id,a.resource_version_id,a.rubric_version_id,b.usergroup_id,b.policy_version_id,b.allow_review FROM ea.submission s JOIN ea.attempt a ON a.id=s.attempt_id JOIN ea.assignment_binding b ON b.id=a.binding_id WHERE s.id=:id AND s.org_id=:org'+(' FOR UPDATE OF s' if lock else ''),id=submission_id,org=actor.org_id))
    if actor.role=='student' and s['student_id']!=actor.user_id:fail('SCOPE_DENIED',403,'只能访问本人的作答')
    await require_course(db,actor,s['course_id'],action=action or ('submission.grade' if actor.role=='teacher' else None),student_id=s['student_id'],class_id=s['usergroup_id'])
    return s


async def grades(db,submission_id):
    return await rows(db,'SELECT g.*,e.score,e.maximum_score,e.source judgment_source,e.feedback FROM ea.grade_revision g JOIN ea.evaluation e ON e.id=g.evaluation_id WHERE g.submission_id=:id ORDER BY g.revision DESC',id=submission_id)


async def review_view(db,review_id):
    row=must(await one(db,'SELECT id,submission_id,grade_revision_id,request_text,status,resolved_revision_id,resolution,resolved_at,created_at FROM ea.review_request WHERE id=:id',id=review_id))
    revision=must(await one(db,'SELECT max(revision) revision FROM ea.grade_revision WHERE submission_id=:id',id=row['submission_id']))
    row['etag']=etag(revision['revision'])
    return clean(row)


async def get_submission(db,actor,submission_id):
    s=await submission_row(db,actor,submission_id)
    history=await grades(db,submission_id);current=history[0] if history else None
    receipt={k:s[k] for k in ['attempt_id','receipt_id','student_id','accepted_at','provenance']};receipt.update(submission_id=s['id'],evaluation_state='effective' if current else 'needs_teacher',etag=etag(current['revision'] if current else 0))
    v=await one(db,'SELECT content FROM ea.resource_version WHERE id=:id',id=s['resource_version_id'])
    result={'receipt':receipt,'binding_id':s['binding_id'],'course_id':s['course_id'],'recipient_id':s['recipient_id'],'answer_snapshot':s['answer_snapshot'],'question_content':v['content'],'resource_version_id':s['resource_version_id'],'rubric_version_id':s['rubric_version_id'],'effective_grade':current,'grade_history':history,'reviews':await rows(db,'SELECT id,grade_revision_id,request_text,status,resolution,resolved_revision_id,created_at FROM ea.review_request WHERE submission_id=:id ORDER BY created_at DESC',id=submission_id),'pipeline':{'evaluation':'effective' if current else 'needs_teacher','profile':'ready' if current else 'not_connected','recommendation':'ready' if current else 'not_connected'},'etag':receipt['etag']}
    if actor.role=='teacher':
        result['candidate_evaluations']=await rows(db,"SELECT id,submission_id,source,outcome,score,maximum_score,details->'item_scores' item_scores,feedback->>'text' feedback,producer_version,created_at FROM ea.evaluation WHERE submission_id=:id ORDER BY created_at DESC",id=submission_id)
    result['reviews']=[await review_view(db,r['id']) for r in result['reviews']]
    if current:
        rec=await one(db,'SELECT state FROM ea.recommendation_version WHERE org_id=:org AND course_id=:course AND student_id=:student ORDER BY created_at DESC LIMIT 1',org=actor.org_id,course=s['course_id'],student=s['student_id'])
        result['pipeline']['recommendation']=rec['state'] if rec else 'not_connected'
    return clean(result)


async def learning_state(db,actor,course_id,student_id):
    await course_row(db,actor,course_id,action='profile.read' if actor.role=='teacher' else None,student_id=student_id)
    if actor.role=='student' and student_id!=actor.user_id:fail('SCOPE_DENIED',403)
    p=await one(db,'SELECT * FROM ea.profile_version WHERE org_id=:org AND course_id=:course AND student_id=:student ORDER BY version DESC LIMIT 1',org=actor.org_id,course=course_id,student=student_id)
    evidence=[]
    if p:
        ids=[e['evidence_id'] for e in p['evidence_manifest']]
        for eid in ids:
            evidence.append(await one(db,'SELECT id,knowledge_point_id,source_type,provenance,judgment_source,summary,observed_at,grade_revision_id FROM ea.evidence_event WHERE id=:id AND org_id=:org AND student_id=:student',id=eid,org=actor.org_id,student=student_id))
    access='allowed'
    if actor.role=='teacher':
        try:await require_course(db,actor,course_id,action='evidence.read',student_id=student_id)
        except HTTPException as exc:
            if exc.status_code!=403:raise
            access='restricted'
    for item in evidence:
        summary=item['summary'];item['summary_text']='有效成绩 '+str(summary.get('score','—'))+' / '+str(summary.get('maximum_score','—'))+'；知识掌握状态未知'
        item['content_access']=access
    stats=await rows(db,'SELECT g.id, e.score,e.maximum_score,e.source FROM ea.grade_revision g JOIN ea.evaluation e ON e.id=g.evaluation_id WHERE g.org_id=:org AND g.course_id=:course AND g.student_id=:student AND NOT EXISTS (SELECT 1 FROM ea.grade_revision next WHERE next.supersedes_id=g.id)',org=actor.org_id,course=course_id,student=student_id)
    return clean({'course_id':course_id,'student_id':student_id,'profile_version_id':p['id'] if p else None,'version':p['version'] if p else 0,'method_kind':'rule_statistics' if p else None,'capability_status':'not_connected','runtime_capability_status':'unconfigured','update_status':'ready' if p else 'not_started','states':p['states'] if p else {},'research_fields':{},'evidence':evidence,'statistics':{'effective_results':len(stats),'score_sum':sum(float(s['score']) for s in stats),'maximum_sum':sum(float(s['maximum_score']) for s in stats),'description':'有效成绩的确定性统计，不是知识掌握概率'},'research_status':'stale' if p and p['trigger_type']=='grade_corrected' else 'not_connected','etag':etag(p['version'] if p else 0),'trigger_type':p['trigger_type'] if p else None,'trigger_event_key':p['trigger_event_key'] if p else None,'trigger_grade_id':p['trigger_grade_id'] if p else None})


async def recommendations(db,actor,course_id,student_id):
    state=await learning_state(db,actor,course_id,student_id)
    r=await one(db,"""SELECT * FROM ea.recommendation_version
        WHERE org_id=:org AND course_id=:course AND student_id=:student
        AND profile_version_id=:profile AND method_version='course-deadline-rule-v1'
        ORDER BY created_at DESC LIMIT 1""",org=actor.org_id,course=course_id,
        student=student_id,profile=state['profile_version_id'])
    items=[]
    if r:
        for item in await rows(db,'SELECT i.*,b.assignment_id,a.title FROM ea.recommendation_item i LEFT JOIN ea.assignment_binding b ON b.id=i.binding_id LEFT JOIN public.assignment a ON a.id=b.assignment_id WHERE recommendation_version_id=:id ORDER BY rank',id=r['id']):
            if item['binding_id']:
                b=await binding_row(db,actor,item['binding_id'])
                if b['status']!='assigned':continue
                current=await one(db,'SELECT action FROM ea.publication_event WHERE resource_version_id=:v AND usergroup_id=:g ORDER BY event_no DESC LIMIT 1',v=b['resource_version_id'],g=b['usergroup_id'])
                if not current or current['action']!='publish':continue
            items.append(item)
    return clean({'id':r['id'] if r else None,'course_id':course_id,'student_id':student_id,'profile_version_id':state['profile_version_id'],'method_version':r['method_version'] if r else None,'strategy':'course_rule' if r else 'none','state':'ready' if items else 'no_content','reason':r['reason'] if r else '完成一次作答并形成有效成绩后生成下一步课程任务建议','items':items,'etag':etag(str(r['id']) if r else '0')})


async def project_grade(db,actor,submission,grade):
    """Append a deterministic evidence projection in the grade transaction."""
    course=submission['course_id'];student=submission['student_id'];org=actor.org_id
    await execute(db,'SELECT pg_advisory_xact_lock(hashtextextended(:key,0))',key=f'profile:{org}:{course}:{student}')
    version=must(await one(db,'SELECT content FROM ea.resource_version WHERE id=:id',id=submission['resource_version_id']))
    kps=version['content'].get('knowledge_point_ids',[])
    evaluation=must(await one(db,'SELECT source,score,maximum_score FROM ea.evaluation WHERE id=:id',id=grade['evaluation_id']))
    for kp in kps:
        await execute(db,"INSERT INTO ea.evidence_event(org_id,course_id,student_id,knowledge_point_id,grade_revision_id,source_type,provenance,judgment_source,source_event_key,summary,observed_at) VALUES(:org,:course,:student,:kp,:grade,'effective_grade',:prov,:source,:key,CAST(:summary AS jsonb),now())",org=org,course=course,student=student,kp=kp,grade=grade['id'],prov=submission['provenance'],source=evaluation['source'],key='grade:'+str(grade['id']),summary=canonical({'score':evaluation['score'],'maximum_score':evaluation['maximum_score'],'kind':'effective_result','mastery':'unknown'}))
    ev=await rows(db,'SELECT e.id,e.knowledge_point_id FROM ea.evidence_event e JOIN ea.grade_revision g ON g.id=e.grade_revision_id WHERE e.org_id=:org AND e.course_id=:course AND e.student_id=:student AND NOT EXISTS (SELECT 1 FROM ea.grade_revision next WHERE next.supersedes_id=g.id) ORDER BY e.created_at',org=org,course=course,student=student)
    states={}
    for e in ev:
        key=str(e['knowledge_point_id']);states.setdefault(key,{'state':'unknown','evidence_ids':[],'reason':'当前仅汇总有效成绩，不据此推断研究掌握状态'})['evidence_ids'].append(str(e['id']))
    previous=await one(db,'SELECT id,version FROM ea.profile_version WHERE org_id=:org AND course_id=:course AND student_id=:student ORDER BY version DESC LIMIT 1 FOR UPDATE',org=org,course=course,student=student)
    task=str(uuid4());profile=str(uuid4());manifest=[{'evidence_id':str(e['id'])} for e in ev]
    await execute(db,"INSERT INTO ea.task_run(id,org_id,course_id,requested_by,operation,idempotency_key,input_hash,input_refs,result_refs,status,attempt_count,max_attempts,completed_at) VALUES(:id,:org,:course,:actor,'projection',:key,:hash,CAST(:input AS jsonb),CAST(:result AS jsonb),'succeeded',1,1,now())",id=task,org=org,course=course,actor=actor.service_id,key='grade:'+str(grade['id']),hash=digest(manifest),input=canonical({'grade_revision_id':grade['id'],'processing':'synchronous'}),result=canonical({'profile_version_id':profile}))
    await execute(db,"INSERT INTO ea.profile_version(id,org_id,course_id,student_id,version,previous_version_id,trigger_type,trigger_event_key,trigger_grade_id,task_id,method_kind,capability_status,method_version,provenance,evidence_manifest,states,input_hash) VALUES(:id,:org,:course,:student,:version,:previous,:trigger,:key,:grade,:task,'rule_statistics','not_connected','effective-grade-statistics-v1',:prov,CAST(:evidence AS jsonb),CAST(:states AS jsonb),:hash)",id=profile,org=org,course=course,student=student,version=previous['version']+1 if previous else 1,previous=previous['id'] if previous else None,trigger='grade_corrected' if grade['revision']>1 else 'grade_effective',key='grade:'+str(grade['id']),grade=grade['id'],task=task,prov=submission['provenance'],evidence=canonical(manifest),states=canonical(states),hash=digest(manifest))
    next_binding=await one(db,"SELECT b.id,r.id recipient_id FROM ea.assignment_binding b JOIN ea.assignment_recipient r ON r.binding_id=b.id WHERE b.org_id=:org AND b.course_id=:course AND r.student_id=:student AND b.status='assigned' AND b.due_at>now() AND NOT EXISTS(SELECT 1 FROM ea.attempt a JOIN ea.submission s ON s.attempt_id=a.id WHERE a.recipient_id=r.id) ORDER BY b.due_at,b.created_at LIMIT 1",org=org,course=course,student=student)
    rec=str(uuid4());can_recommend=bool(next_binding and ev)
    await execute(db,"INSERT INTO ea.recommendation_version(id,org_id,course_id,student_id,profile_version_id,method_version,task_id,provenance,state,reason) VALUES(:id,:org,:course,:student,:profile,'course-deadline-rule-v1',:task,:prov,:state,:reason)",id=rec,org=org,course=course,student=student,profile=profile,task=task,prov=submission['provenance'],state='ready' if can_recommend else 'no_content',reason='按课程待交任务及截止时间排序；此为课程规则，不是正式B推荐' if can_recommend else '当前没有新的待交任务，可查看反馈或申请订正')
    if can_recommend:
        await execute(db,'INSERT INTO ea.recommendation_item(org_id,course_id,student_id,recommendation_version_id,rank,knowledge_point_id,evidence_id,binding_id,recipient_id,reason) VALUES(:org,:course,:student,:rec,1,:kp,:ev,:binding,:recipient,:reason)',org=org,course=course,student=student,rec=rec,kp=ev[-1]['knowledge_point_id'],ev=ev[-1]['id'],binding=next_binding['id'],recipient=next_binding['recipient_id'],reason='继续完成当前课程最早截止的未提交任务')
    await execute(db,"INSERT INTO ea.outbox_event(org_id,course_id,event_type,aggregate_type,aggregate_id,deduplication_key,payload,published_at) VALUES(:org,:course,'grade.projected','grade_revision',:grade,:key,CAST(:payload AS jsonb),now())",org=org,course=course,grade=grade['id'],key=str(grade['id']),payload=canonical({'grade_revision_id':grade['id'],'profile_version_id':profile,'recommendation_version_id':rec,'processing':'synchronous'}))


async def add_grade(db,actor,s,evaluation_id,reason,expected,supersedes,service=False):
    history=await grades(db,s['id']);previous=history[0] if history else None
    if expected!=(previous['revision'] if previous else 0) or str(supersedes or '')!=str(previous['id'] if previous else ''):fail('VERSION_CONFLICT',409,'有效成绩已经变化')
    e=must(await one(db,'SELECT * FROM ea.evaluation WHERE id=:id AND submission_id=:s',id=evaluation_id,s=s['id']))
    if e['source']!=('rule' if service else 'teacher') or e['outcome']!='scored':fail('EVALUATION_NOT_APPLICABLE',409,'只有明确确认的评分可以生效')
    g=must(await one(db,'INSERT INTO ea.grade_revision(org_id,course_id,submission_id,student_id,evaluation_id,revision,supersedes_id,reason,actor_id) VALUES(:org,:course,:submission,:student,:evaluation,:revision,:previous,:reason,:actor) RETURNING *',org=actor.org_id,course=s['course_id'],submission=s['id'],student=s['student_id'],evaluation=evaluation_id,revision=expected+1,previous=previous['id'] if previous else None,reason=reason,actor=actor.service_id if service else actor.principal_id))
    await project_grade(db,actor,s,g)
    return g
