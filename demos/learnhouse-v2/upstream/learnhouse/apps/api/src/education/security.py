"""Server-owned identity and live object scopes for the isolated education demo."""
from dataclasses import dataclass
import os
from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession
from src.core.events.database import get_db_session
from src.db.users import PublicUser
from src.security.auth import get_current_user

TEACHING_ACTIONS = {'course.create','course.edit','content.read','content.generate','content.edit','content.review','content.publish','task.manage','submission.grade','result.review','profile.read','intervention.write','evidence.read','export.read'}
ADMIN_ACTIONS = {'access.manage','policy.manage','operations.manage','audit.metadata'}

def fail(code: str, status: int = 400, message: str = ''):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message or code})


@dataclass(frozen=True)
class Actor:
    principal_id: str
    user_id: int
    org_id: int
    role: str
    service_id: str
    name: str
    email: str
    grants: tuple


async def require_actor(request: Request, db: AsyncSession = Depends(get_db_session)) -> Actor:
    if not request.cookies.get('LH_access') or request.headers.get('authorization'):
        fail('AUTHENTICATION_REQUIRED', 401)
    user = await get_current_user(request, db)
    if not isinstance(user, PublicUser):
        fail('HUMAN_SESSION_REQUIRED', 403)
    row = (await db.execute(text('''
      SELECT p.id,p.org_id,p.disabled_at,u.id AS user_id,u.username,u.email,
             u.is_superadmin,uo.role_id
      FROM ea.principal p JOIN public."user" u ON u.id=p.user_id
      JOIN public.organization o ON o.id=p.org_id
      JOIN public.userorganization uo ON uo.org_id=p.org_id AND uo.user_id=p.user_id
      WHERE p.kind='human' AND p.user_id=:uid AND o.slug=:slug
      FOR SHARE OF p,uo
    '''), {'uid': user.id, 'slug': os.getenv('EA_ORG_SLUG', 'default')})).mappings().first()
    return await _actor_from_row(db, row)


async def require_current_actor(db, actor: Actor) -> Actor:
    """Reload a retained worker identity; a previous HTTP session is not authority.

    Keep identity and membership row locks until the caller ends its transaction.
    In particular, candidate import must commit while these checks still hold.
    """
    row = (await db.execute(text('''
      SELECT p.id,p.org_id,p.disabled_at,u.id AS user_id,u.username,u.email,
             u.is_superadmin,uo.role_id
      FROM ea.principal p JOIN public."user" u ON u.id=p.user_id
      JOIN public.userorganization uo ON uo.org_id=p.org_id AND uo.user_id=p.user_id
      WHERE p.kind='human' AND p.id=CAST(:p AS uuid) AND p.org_id=:o AND p.user_id=:u
      FOR SHARE OF p,uo,u
    '''), {'p': actor.principal_id, 'o': actor.org_id, 'u': actor.user_id})).mappings().first()
    return await _actor_from_row(db, row)


async def _actor_from_row(db, row) -> Actor:
    if not row or row['disabled_at']:
        fail('PRINCIPAL_DISABLED_OR_UNASSIGNED', 403)
    if row['is_superadmin'] or row['role_id'] in (1, 2):
        fail('UPSTREAM_PRIVILEGED_ROLE_NOT_ALLOWED', 403)
    grants = (await db.execute(text('''SELECT * FROM ea.action_grant
      WHERE principal_id=CAST(:p AS uuid) AND org_id=:o AND revoked_at IS NULL
      AND starts_at<=now() AND (expires_at IS NULL OR expires_at>now())
      ORDER BY action,id FOR SHARE'''), {'p': str(row['id']), 'o': row['org_id']})).mappings().all()
    actions = {g['action'] for g in grants}
    role = 'admin' if actions & ADMIN_ACTIONS else ('teacher' if actions & TEACHING_ACTIONS else 'student')
    service = (await db.execute(text("SELECT id FROM ea.principal WHERE org_id=:o AND kind='service' AND service_name='education-demo-worker' AND disabled_at IS NULL"), {'o': row['org_id']})).scalar_one_or_none()
    if not service:
        fail('SERVICE_NOT_CONFIGURED', 503)
    return Actor(str(row['id']), row['user_id'], row['org_id'], role, str(service), row['username'], row['email'], tuple(dict(g) for g in grants))


async def _course(db, actor, course_id):
    row = (await db.execute(text('SELECT id,org_id,course_uuid FROM public.course WHERE id=:c AND org_id=:o'), {'c': course_id, 'o': actor.org_id})).mappings().first()
    if not row:
        fail('SCOPE_DENIED', 403)
    return row


async def allowed_class_ids(db, actor, course_id, action=None):
    await _course(db, actor, course_id)
    if actor.role == 'admin':
        fail('SCOPE_DENIED', 403)
    if actor.role == 'student':
        rows = (await db.execute(text('''SELECT DISTINCT ug.id FROM public.usergroup ug
          JOIN public.usergroupuser m ON m.usergroup_id=ug.id AND m.org_id=ug.org_id
          JOIN public.usergroupresource r ON r.usergroup_id=ug.id AND r.org_id=ug.org_id
          JOIN public.course c ON c.course_uuid=r.resource_uuid AND c.org_id=r.org_id
          WHERE c.id=:c AND c.org_id=:o AND m.user_id=:u
          AND (m.expires_at IS NULL OR m.expires_at>now())
          AND (r.starts_at IS NULL OR r.starts_at<=now())
          AND (r.expires_at IS NULL OR r.expires_at>now())'''), {'c': course_id, 'o': actor.org_id, 'u': actor.user_id})).scalars().all()
    else:
        rows = (await db.execute(text('''SELECT DISTINCT ug.id FROM public.usergroup ug
          JOIN public.usergroupresource r ON r.usergroup_id=ug.id AND r.org_id=ug.org_id
          JOIN public.course c ON c.course_uuid=r.resource_uuid AND c.org_id=r.org_id
          JOIN ea.action_grant g ON g.org_id=c.org_id AND g.course_id=c.id
          WHERE c.id=:c AND c.org_id=:o AND g.principal_id=CAST(:p AS uuid)
          AND g.action=:a AND g.student_id IS NULL
          AND (g.usergroup_id IS NULL OR g.usergroup_id=ug.id)
          AND g.revoked_at IS NULL AND g.starts_at<=now() AND (g.expires_at IS NULL OR g.expires_at>now())
          AND (r.starts_at IS NULL OR r.starts_at<=now())
          AND (r.expires_at IS NULL OR r.expires_at>now())'''), {'c':course_id,'o':actor.org_id,'p':actor.principal_id,'a':action or 'content.read'})).scalars().all()
    return list(rows)


async def require_course(db, actor, course_id, action=None, student_id=None, class_id=None):
    course = await _course(db, actor, course_id)
    if actor.role == 'admin':
        fail('SCOPE_DENIED', 403)
    if actor.role == 'student':
        if student_id is not None and student_id != actor.user_id:
            fail('SCOPE_DENIED', 403)
        if action and action not in {'content.read','profile.read','evidence.read'}:
            fail('SCOPE_DENIED', 403)
        groups = await allowed_class_ids(db, actor, course_id)
        if not groups or (class_id is not None and class_id not in groups):
            fail('SCOPE_DENIED', 403)
        subject = actor.user_id
    else:
        grant = (await db.execute(text('''SELECT g.id FROM ea.action_grant g
          WHERE g.org_id=:o AND g.course_id=:c AND g.principal_id=CAST(:p AS uuid)
          AND g.action=:a AND g.revoked_at IS NULL AND g.starts_at<=now()
          AND (g.expires_at IS NULL OR g.expires_at>now())
          AND (g.usergroup_id IS NULL OR g.usergroup_id=:cl OR
               (CAST(:cl AS integer) IS NULL AND CAST(:st AS integer) IS NOT NULL AND EXISTS (
                 SELECT 1 FROM public.usergroupuser m WHERE m.usergroup_id=g.usergroup_id
                 AND m.org_id=g.org_id AND m.user_id=:st AND (m.expires_at IS NULL OR m.expires_at>now()))))
          AND (g.student_id IS NULL OR g.student_id=:st)
          FOR SHARE OF g'''), {'o':actor.org_id,'c':course_id,'p':actor.principal_id,'a':action or 'content.read','cl':class_id,'st':student_id})).first()
        # For a course overview, any class-wide grant can reveal course metadata;
        # callers must still filter rows with allowed_class_ids and recheck details.
        if not grant and student_id is None and class_id is None:
            groups = await allowed_class_ids(db, actor, course_id, action)
            if not groups:
                fail('SCOPE_DENIED', 403)
        elif not grant:
            fail('SCOPE_DENIED', 403)
        subject = student_id
    if subject is not None:
        member = (await db.execute(text('''SELECT m.id FROM public.usergroupuser m
          JOIN public.usergroupresource r ON r.usergroup_id=m.usergroup_id AND r.org_id=m.org_id
          JOIN public.userorganization uo ON uo.user_id=m.user_id AND uo.org_id=m.org_id
          JOIN public.course c ON c.course_uuid=r.resource_uuid AND c.org_id=r.org_id
          JOIN ea.principal p ON p.user_id=m.user_id AND p.org_id=m.org_id AND p.kind='human'
          WHERE c.id=:c AND c.org_id=:o AND m.user_id=:u AND p.disabled_at IS NULL
          AND (CAST(:cl AS integer) IS NULL OR m.usergroup_id=:cl)
          AND (m.expires_at IS NULL OR m.expires_at>now())
          AND (r.starts_at IS NULL OR r.starts_at<=now()) AND (r.expires_at IS NULL OR r.expires_at>now())
          FOR SHARE OF m,r,uo,p'''), {'c':course_id,'o':actor.org_id,'u':subject,'cl':class_id})).first()
        if not member:
            fail('SCOPE_DENIED', 403)
    elif class_id is not None:
        if class_id not in await allowed_class_ids(db, actor, course_id, action):
            fail('SCOPE_DENIED', 403)
    return dict(course)


def require_admin(actor, action):
    if actor.role != 'admin' or not any(g['action']==action and g['course_id'] is None for g in actor.grants):
        fail('SCOPE_DENIED', 403)


class EducationDemoBoundary:
    """Fail-closed legacy-route protection for a dedicated V2 demo instance.

    This intentionally does not claim to support side-by-side legacy LMS use.
    It covers unauthenticated/direct-content and privileged legacy routes too.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http','websocket'} or os.getenv('EA_DEMO_MODE')!='1':
            return await self.app(scope, receive, send)
        path=scope.get('path','').rstrip('/')
        allowed=path.startswith('/api/v1/education/') or path=='/api/v1/education' or path in {
            '/api/v1/auth/login','/api/v1/auth/logout','/api/v1/auth/refresh','/api/v1/auth/me',
            '/api/v1/health','/api/v1/instance/info', '/health'
        }
        if scope['type']=='websocket':
            await send({'type':'websocket.close','code':1008});return
        from starlette.responses import JSONResponse
        if not allowed:
            return await JSONResponse({'detail':{'code':'LEGACY_ROUTE_DISABLED','message':'请使用教育智能体入口'}},status_code=403)(scope,receive,send)
        if scope['method'] in {'POST','PUT','PATCH','DELETE'}:
            headers={k.lower():v for k,v in scope.get('headers',[])}
            origin=headers.get(b'origin',b'').decode('latin1')
            origins=set(os.getenv('EA_ALLOWED_ORIGINS','http://localhost:3300,http://127.0.0.1:3300').split(','))
            if origin not in origins:
                return await JSONResponse({'detail':{'code':'ORIGIN_DENIED','message':'写操作需要可信来源'}},status_code=403)(scope,receive,send)
        return await self.app(scope,receive,send)
