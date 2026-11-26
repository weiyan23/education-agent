-- Only applied to a fresh temporary PostgreSQL instance by the isolated check.
ALTER TABLE public.assignment ADD COLUMN title text, ADD COLUMN description text;
ALTER TABLE public.course ADD COLUMN name text, ADD COLUMN description text,
    ADD COLUMN learnings text, ADD COLUMN published boolean, ADD COLUMN update_date timestamptz;
INSERT INTO public.organization VALUES(1);
INSERT INTO public."user" VALUES(1),(2);
INSERT INTO public.course(id,org_id,course_uuid) VALUES(10,1,'qa-java');
INSERT INTO public.assignment(id,course_id,org_id,title) VALUES(20,10,1,'QA Java');
INSERT INTO public.usergroup VALUES(11,1);
INSERT INTO public.userorganization(user_id,org_id) VALUES(1,1),(2,1);
INSERT INTO public.usergroupuser(usergroup_id,user_id,org_id) VALUES(11,2,1);
INSERT INTO public.usergroupresource(usergroup_id,org_id,resource_uuid) VALUES(11,1,'qa-java');
INSERT INTO public.learner_note VALUES(30,2,10,1);
INSERT INTO ea.principal(org_id,kind,user_id) VALUES(1,'human',1),(1,'human',2);
DO $$
DECLARE
    teacher uuid; parent uuid; policy uuid; kp uuid; resource uuid; version uuid; rubric uuid;
    checker uuid; reviewer uuid; pub uuid; binding uuid; consent uuid;
BEGIN
    SELECT id INTO teacher FROM ea.principal WHERE user_id=1;
    INSERT INTO ea.policy_version(org_id,family_id,version,policy_type,created_by,content_hash)
        VALUES(1,gen_random_uuid(),1,'global',teacher,repeat('d',64)) RETURNING id INTO parent;
    INSERT INTO ea.policy_version(org_id,course_id,family_id,version,parent_policy_id,policy_type,created_by,content_hash)
        VALUES(1,10,gen_random_uuid(),1,parent,'course',teacher,repeat('a',64)) RETURNING id INTO policy;
    INSERT INTO ea.knowledge_point(org_id,course_id,code,label) VALUES(1,10,'java.condition','Java 条件判断') RETURNING id INTO kp;
    INSERT INTO ea.resource(org_id,course_id,kind,title,owner_id) VALUES(1,10,'exercise','QA Java',teacher) RETURNING id INTO resource;
    INSERT INTO ea.resource_version(org_id,course_id,resource_id,version,policy_version_id,content_hash,processing_state,
        generation_mode,provenance,created_by,content) VALUES(1,10,resource,1,policy,repeat('b',64),'ready','teacher','demo',teacher,
        jsonb_build_object('knowledge_point_ids',jsonb_build_array(kp),'questions',jsonb_build_array(jsonb_build_object('prompt','QA')))) RETURNING id INTO version;
    INSERT INTO ea.rubric_version(org_id,course_id,family_id,version,maximum_score,content_hash,created_by)
        VALUES(1,10,gen_random_uuid(),1,100,repeat('c',64),teacher) RETURNING id INTO rubric;
    INSERT INTO ea.content_check(org_id,course_id,resource_version_id,policy_version_id,check_kind,verdict,checker_version,actor_id)
        VALUES(1,10,version,policy,'deterministic','pass','qa',teacher) RETURNING id INTO checker;
    INSERT INTO ea.content_check(org_id,course_id,resource_version_id,policy_version_id,check_kind,verdict,checker_version,actor_id,reason)
        VALUES(1,10,version,policy,'human_review','approve','qa',teacher,'synthetic review') RETURNING id INTO reviewer;
    INSERT INTO ea.publication_event(org_id,course_id,resource_version_id,usergroup_id,event_no,action,validation_id,review_id,policy_version_id,actor_id,reason)
        VALUES(1,10,version,11,1,'publish',checker,reviewer,policy,teacher,'fixture') RETURNING id INTO pub;
    INSERT INTO ea.assignment_binding(org_id,course_id,assignment_id,version,resource_version_id,rubric_version_id,policy_version_id,publication_event_id,
        usergroup_id,opens_at,due_at,created_by) VALUES(1,10,20,1,version,rubric,policy,pub,11,now()-interval '1 day',now()+interval '1 day',teacher)
        RETURNING id INTO binding;
    INSERT INTO ea.assignment_recipient(org_id,course_id,binding_id,student_id,usergroup_id) VALUES(1,10,binding,2,11);
    INSERT INTO ea.note_permission(org_id,course_id,note_id,student_id,purpose,consent_revision)
        VALUES(1,10,30,2,'profile_use',1) RETURNING id INTO consent;
    INSERT INTO ea.evidence_event(org_id,course_id,student_id,knowledge_point_id,note_permission_id,source_type,provenance,judgment_source,
        source_event_key,observed_at) VALUES(1,10,2,kp,consent,'consented_note','demo','self_report','qa-note',now());
END $$;
