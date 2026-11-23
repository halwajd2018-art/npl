from __future__ import annotations
import os, re, sys, io, hmac, csv, json, time, uuid, sqlite3, secrets, hashlib, logging, zipfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.concurrency import run_in_threadpool
from .ingest import INGESTERS, save_json,load_json, BASE_TARGETS
from .reports import report1,report2,report3

APP_DIR=Path(__file__).resolve().parent
DATA_DIR=Path(os.environ.get('DATA_DIR',str(APP_DIR.parent/'data'))).resolve()
UPLOAD_DIR=DATA_DIR/'uploads';CACHE_DIR=DATA_DIR/'processed';DB=DATA_DIR/'app.sqlite3';MAX_MB=int(os.environ.get('MAX_UPLOAD_MB','130'))
KIND_NAMES={'branch_pdf':'تقرير نشاط الفروع PDF','targets':'المستهدف السنوي Excel','payments':'حركات السداد Excel','contracts':'بيانات العقود الإضافية Excel','classification':'التصنيف الأساسي للقروض Excel','portfolio':'محفظة القروض الشهرية Excel','rescheduling':'تقرير الجدولة التفصيلي Excel'}
PERMISSIONS={'viewer':('view',),'uploader':('view','upload'),'editor':('view','upload','edit','delete'),'admin':('view','upload','edit','delete','users')}
AUDIT_LOG=logging.getLogger(__name__)

def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def sql():
    db=sqlite3.connect(DB,timeout=60)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=60000')
    return db

def bootstrap_db():
    for p in [DATA_DIR,UPLOAD_DIR,CACHE_DIR]:p.mkdir(parents=True,exist_ok=True)
    try:os.chmod(DATA_DIR,0o700)
    except OSError:pass
    with sql() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,display_name TEXT NOT NULL,
          password_hash TEXT NOT NULL,role TEXT NOT NULL,permissions TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,failed_attempts INTEGER NOT NULL DEFAULT 0,
          locked_until REAL DEFAULT 0,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS uploads (
          id TEXT PRIMARY KEY,kind TEXT NOT NULL,original_name TEXT NOT NULL,
          reference_period TEXT NOT NULL,uploaded_at TEXT NOT NULL,uploaded_by INTEGER NOT NULL,
          file_path TEXT NOT NULL,processed_path TEXT NOT NULL,sha256 TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,row_count INTEGER NOT NULL,source_period TEXT,
          metadata TEXT NOT NULL,deleted_at TEXT,deleted_by INTEGER,
          FOREIGN KEY(uploaded_by) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS audits (
          id INTEGER PRIMARY KEY, actor_id INTEGER, action TEXT NOT NULL,
          entity_kind TEXT,entity_id TEXT,details TEXT,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_uploads_kind_period ON uploads(kind,reference_period,uploaded_at);
        ''')

def hash_pw(password):
    salt=secrets.token_bytes(16)
    raw=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
    return 'scrypt$'+salt.hex()+'$'+raw.hex()

def check_pw(password,stored):
    try:
        alg,salt,out=stored.split('$')
        if alg!='scrypt':return False
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32)
        return hmac.compare_digest(actual.hex(),out)
    except (ValueError,TypeError):return False

def user_count():
    with sql() as db:return db.execute('SELECT COUNT(*) FROM users').fetchone()[0]

def create_admin(username,password,display_name='مدير النظام'):
    if len(password)<12:raise ValueError('كلمة مرور المدير يجب ألا تقل عن 12 حرفاً.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}',username):raise ValueError('اسم المستخدم يجب أن يكون حروفاً لاتينية أو أرقاماً (3-40).')
    with sql() as db:
        db.execute('INSERT INTO users (username,display_name,password_hash,role,permissions,created_at) VALUES (?,?,?,?,?,?)',
                   (username,display_name,hash_pw(password),'admin',','.join(PERMISSIONS['admin']),now()))

def audit(actor,action,kind=None,entity_id=None,details=''):
    with sql() as db:db.execute('INSERT INTO audits (actor_id,action,entity_kind,entity_id,details,created_at) VALUES (?,?,?,?,?,?)',(actor,action,kind,entity_id,details,now()))

@asynccontextmanager
async def lifespan(app:FastAPI):
    bootstrap_db()
    # A hosted instance must never start without an administrator. Credentials
    # are supplied as one-time secrets by the hosting provider. After the first
    # successful boot they are ignored; they are never included in source files.
    if user_count() == 0 and os.environ.get('APP_ENV') == 'production':
        username = os.environ.get('INITIAL_ADMIN_USERNAME', '').strip()
        password = os.environ.get('INITIAL_ADMIN_PASSWORD', '')
        if not username or not password:
            raise RuntimeError('Set INITIAL_ADMIN_USERNAME and INITIAL_ADMIN_PASSWORD as secret environment variables before the first production launch.')
        create_admin(username, password, 'مدير النظام')
    yield

app=FastAPI(title='صندوق التنمية الزراعية | منصة التقارير',docs_url=None,redoc_url=None,lifespan=lifespan)
DATA_DIR.mkdir(parents=True,exist_ok=True)
if os.environ.get('APP_ENV')=='production' and not os.environ.get('SESSION_SECRET'):
    raise RuntimeError('SESSION_SECRET is required in production')
secret_file=DATA_DIR/'.session_key'
if os.environ.get('SESSION_SECRET'):session_secret=os.environ['SESSION_SECRET']
elif secret_file.exists():session_secret=secret_file.read_text().strip()
else:
    session_secret=secrets.token_urlsafe(64)
    secret_file.write_text(session_secret)
    try:os.chmod(secret_file,0o600)
    except OSError:pass
app.add_middleware(SessionMiddleware,secret_key=session_secret,session_cookie='adf_session',
                   https_only=os.environ.get('APP_ENV')=='production',same_site='lax',max_age=3600*8)
app.mount('/static',StaticFiles(directory=APP_DIR/'static'),name='static')

@app.get('/health', include_in_schema=False)
def health():
    try:
        if user_count() < 1:
            raise HTTPException(503, 'not initialized')
        return {'status': 'ok'}
    except sqlite3.Error:
        raise HTTPException(503, 'database unavailable')


@app.get('/manifest.webmanifest',include_in_schema=False)
def pwa_manifest():
    return FileResponse(APP_DIR/'static'/'manifest.webmanifest',media_type='application/manifest+json')

@app.get('/sw.js',include_in_schema=False)
def pwa_worker():
    return FileResponse(APP_DIR/'static'/'sw.js',media_type='application/javascript',headers={'Service-Worker-Allowed':'/','Cache-Control':'no-store'})

tmpl=Jinja2Templates(directory=APP_DIR/'templates')
tmpl.env.filters['money']=lambda n: f'{float(n or 0):,.2f}'
tmpl.env.filters['integer']=lambda n: f'{int(n or 0):,}'
tmpl.env.filters['time_sa']=lambda s: (datetime.fromisoformat(s).astimezone(timezone(timedelta(hours=3))).strftime('%Y-%m-%d %H:%M') if s else '—')

@app.middleware('http')
async def response_security(request,call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Cache-Control']='no-store'
    response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    if os.environ.get('APP_ENV')=='production':response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    return response

def current_user(request:Request):
    uid=request.session.get('uid')
    if not uid:return None
    with sql() as db:u=db.execute('SELECT * FROM users WHERE id=? AND active=1',(uid,)).fetchone()
    return dict(u) if u else None

def auth(request:Request):
    u=current_user(request)
    if not u:raise HTTPException(401,'يلزم تسجيل الدخول.')
    return u

def allow(u,permission):
    if permission not in u['permissions'].split(','):raise HTTPException(403,'الصلاحية غير متاحة لهذا المستخدم.')

def csrf(request:Request):
    token=request.session.get('csrf')
    if not token:token=secrets.token_urlsafe(32);request.session['csrf']=token
    return token

def check_csrf(request,submitted):
    if not submitted or not hmac.compare_digest(str(submitted),str(request.session.get('csrf',''))):
        raise HTTPException(403,'فشل التحقق من الطلب. حدث الصفحة وحاول مجدداً.')

def page(request, name, **data):
    u=current_user(request)
    context={'request':request,'user':u,'csrf':csrf(request),'kinds':KIND_NAMES,'permissions':(u['permissions'].split(',') if u else []),**data}
    return tmpl.TemplateResponse(name,context)

def valid_period(kind,value,ingested):
    s=(value or '').strip()
    if not re.fullmatch(r'20\d{2}(?:-(?:0[1-9]|1[0-2]))?(?:-(?:0[1-9]|[12]\d|3[01]))?',s):
        raise ValueError('الفترة المرجعية بصيغة 2026 أو 2026-10 أو 2026-10-31.')
    if kind=='branch_pdf':
        detected=ingested.get('period')
        if detected and not detected.startswith(s):
            raise ValueError(f'الفترة المدخلة {s} لا تطابق فترة PDF المكتشفة {detected}.')
    if kind=='targets' and len(s)!=4:raise ValueError('ملف المستهدف السنوي يجب أن يحمل سنة مالية مثل 2026.')
    if kind in ('portfolio','payments','rescheduling') and len(s)<7:raise ValueError('حدد شهراً للفترة مثل 2026-10.')
    return s

def list_uploads(kind=None):
    with sql() as db:
        if kind:return [dict(r) for r in db.execute('SELECT u.*, x.display_name FROM uploads u LEFT JOIN users x ON x.id=u.uploaded_by WHERE u.deleted_at IS NULL AND kind=? ORDER BY uploaded_at DESC,id DESC',(kind,)).fetchall()]
        return [dict(r) for r in db.execute('SELECT u.*,x.display_name FROM uploads u LEFT JOIN users x ON x.id=u.uploaded_by WHERE u.deleted_at IS NULL ORDER BY uploaded_at DESC,id DESC').fetchall()]

def get_upload(id,kind=None):
    with sql() as db:u=db.execute('SELECT * FROM uploads WHERE id=? AND deleted_at IS NULL',(id,)).fetchone()
    if not u or (kind and u['kind']!=kind):raise HTTPException(404,'لم يتم العثور على ملف نشط من النوع المطلوب.')
    return dict(u)

def load_data(upload_id,kind):
    u=get_upload(upload_id,kind)
    p=Path(u['processed_path'])
    if not p.is_file():raise HTTPException(410,'الملف المعالج غير موجود؛ أعد رفع النسخة.')
    return load_json(p)

@app.get('/',response_class=HTMLResponse)
def home(request:Request):
    u=current_user(request)
    if not u:return RedirectResponse('/login',303)
    allow(u,'view')
    uploads=list_uploads()
    by_kind={k:sum(1 for x in uploads if x['kind']==k) for k in KIND_NAMES}
    with sql() as db:
        totals={'users':db.execute('SELECT COUNT(*) FROM users WHERE active=1').fetchone()[0],
                'recent_audits':db.execute('SELECT COUNT(*) FROM audits').fetchone()[0]}
    return page(request,'dashboard.html',uploads=uploads[:8],by_kind=by_kind,totals=totals)

@app.get('/login',response_class=HTMLResponse)
def login_get(request:Request):
    if current_user(request):return RedirectResponse('/',303)
    return page(request,'login.html',error=None,has_admin=user_count()>0)

@app.post('/login')
def login_post(request:Request,username:str=Form(...),password:str=Form(...),csrf_token:str=Form(...)):
    check_csrf(request,csrf_token)
    with sql() as db:
        u=db.execute('SELECT * FROM users WHERE username=?',(username.strip(),)).fetchone()
        ok=bool(u and u['active'] and (u['locked_until'] or 0)<=time.time() and check_pw(password,u['password_hash']))
        if not ok:
            if u and u['active']:
                attempts=u['failed_attempts']+1
                db.execute('UPDATE users SET failed_attempts=?,locked_until=? WHERE id=?',(attempts,time.time()+900 if attempts>=5 else 0,u['id']))
            return page(request,'login.html',error='بيانات الدخول غير صحيحة أو الحساب مقفل مؤقتاً.',has_admin=True)
        db.execute('UPDATE users SET failed_attempts=0,locked_until=0 WHERE id=?',(u['id'],))
    request.session.clear();request.session['uid']=u['id'];csrf(request)
    audit(u['id'],'login','user',str(u['id']))
    return RedirectResponse('/',303)

@app.post('/logout')
def logout(request:Request,csrf_token:str=Form(...)):
    check_csrf(request,csrf_token);request.session.clear()
    return RedirectResponse('/login',303)

@app.get('/files',response_class=HTMLResponse)
def files(request:Request,u=Depends(auth)):
    allow(u,'view')
    return page(request,'files.html',uploads=list_uploads(),error=None)

@app.get('/uploads/new',response_class=HTMLResponse)
def upload_new(request:Request,u=Depends(auth)):
    allow(u,'upload')
    return page(request,'upload.html',error=None,submitted_kind='branch_pdf',submitted_period='2026-10')

@app.post('/uploads/new',response_class=HTMLResponse)
async def upload_post(request:Request,kind:str=Form(...),reference_period:str=Form(...),file:UploadFile=File(...),csrf_token:str=Form(...),u=Depends(auth)):
    allow(u,'upload');check_csrf(request,csrf_token)
    if kind not in INGESTERS:raise HTTPException(400,'نوع الملف غير صالح.')
    name=Path(file.filename or '').name
    ext=Path(name).suffix.lower()
    if ext!=('.pdf' if kind=='branch_pdf' else '.xlsx'):
        return page(request,'upload.html',error='امتداد الملف غير مطابق لنوع التقرير.',submitted_kind=kind,submitted_period=reference_period)
    uid=uuid.uuid4().hex
    raw=UPLOAD_DIR/f'{uid}{ext}'; processed=CACHE_DIR/f'{uid}.json.gz'
    hasher=hashlib.sha256();size=0
    try:
        with open(raw,'wb') as out:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>MAX_MB*1024*1024:raise ValueError(f'حجم الملف يتجاوز {MAX_MB} ميجابايت.')
                hasher.update(chunk);out.write(chunk)
        if size<100:raise ValueError('الملف المرفوع فارغ أو غير صالح.')
        result=await run_in_threadpool(INGESTERS[kind],raw)
        period=valid_period(kind,reference_period,result)
        await run_in_threadpool(save_json,processed,result)
        if kind=='payments':count=result['count']
        elif kind=='contracts':count=result['count']
        else:count=result.get('count',len(result.get('rows',{})))
        meta={k:v for k,v in result.items() if k in ('total','total_actual','total_food','total_writeoff','total','duplicates','skipped','scanned','unique','period')}
        with sql() as db:
            db.execute('INSERT INTO uploads (id,kind,original_name,reference_period,uploaded_at,uploaded_by,file_path,processed_path,sha256,size_bytes,row_count,source_period,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (uid,kind,name,period,now(),u['id'],str(raw),str(processed),hasher.hexdigest(),size,count,result.get('period'),json.dumps(meta,ensure_ascii=False)))
        audit(u['id'],'upload',kind,uid,f'{name} | {period} | {size} bytes')
        return RedirectResponse('/files?uploaded=1',303)
    except Exception as ex:
        raw.unlink(missing_ok=True);processed.unlink(missing_ok=True)
        AUDIT_LOG.exception('File ingestion failed: %s',name)
        msg=str(ex) if isinstance(ex,(ValueError,zipfile.BadZipFile)) else 'تعذر قراءة الملف. تحقق من نوعه وحقوله ثم أعد المحاولة.'
        return page(request,'upload.html',error=msg,submitted_kind=kind,submitted_period=reference_period)
    finally:await file.close()

@app.get('/uploads/{upload_id}/edit',response_class=HTMLResponse)
def edit_upload_get(request:Request,upload_id:str,u=Depends(auth)):
    allow(u,'edit');return page(request,'edit_upload.html',item=get_upload(upload_id),error=None)

@app.post('/uploads/{upload_id}/edit')
def edit_upload_post(request:Request,upload_id:str,reference_period:str=Form(...),csrf_token:str=Form(...),u=Depends(auth)):
    allow(u,'edit');check_csrf(request,csrf_token);item=get_upload(upload_id)
    try:
        data=load_data(upload_id,item['kind']);period=valid_period(item['kind'],reference_period,data)
        with sql() as db:db.execute('UPDATE uploads SET reference_period=? WHERE id=?',(period,upload_id))
        audit(u['id'],'edit_metadata',item['kind'],upload_id,f"{item['reference_period']} -> {period}")
        return RedirectResponse('/files',303)
    except ValueError as ex:return page(request,'edit_upload.html',item=item,error=str(ex))

@app.post('/uploads/{upload_id}/delete')
def delete_upload(request:Request,upload_id:str,csrf_token:str=Form(...),u=Depends(auth)):
    allow(u,'delete');check_csrf(request,csrf_token)
    item=get_upload(upload_id)
    # Delete from both on-disk stores: keeping only the audit event and a soft-deleted metadata tombstone.
    for p in (item['file_path'],item['processed_path']):Path(p).unlink(missing_ok=True)
    with sql() as db:db.execute('UPDATE uploads SET deleted_at=?,deleted_by=? WHERE id=?',(now(),u['id'],upload_id))
    audit(u['id'],'delete',item['kind'],upload_id,item['original_name']+' | '+item['reference_period'])
    return RedirectResponse('/files?deleted=1',303)

@app.get('/admin/users',response_class=HTMLResponse)
def users_list(request:Request,u=Depends(auth)):
    allow(u,'users')
    with sql() as db:users=[dict(x) for x in db.execute('SELECT * FROM users ORDER BY id DESC')]
    return page(request,'users.html',users=users,error=None)

@app.post('/admin/users/create')
def user_create(request:Request,username:str=Form(...),display_name:str=Form(...),password:str=Form(...),role:str=Form(...),permissions:str=Form(''),permission_flags:list[str]=Form([]),csrf_token:str=Form(...),u=Depends(auth)):
    allow(u,'users');check_csrf(request,csrf_token)
    if role not in PERMISSIONS and role!='custom':raise HTTPException(400,'دور غير صالح')
    if len(password)<12:raise HTTPException(400,'كلمة المرور يجب أن تحتوي على 12 حرفاً على الأقل.')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}',username):raise HTTPException(400,'اسم مستخدم غير صالح')
    grants=[p for p in (permission_flags or permissions.split(',')) if p in ('view','upload','edit','delete','users')] if role=='custom' else list(PERMISSIONS[role])
    if role=='admin':grants=list(PERMISSIONS['admin'])
    try:
        with sql() as db:
            cur=db.execute('INSERT INTO users (username,display_name,password_hash,role,permissions,created_at) VALUES (?,?,?,?,?,?)',
                           (username.strip(),display_name.strip(),hash_pw(password),role,','.join(grants),now()))
        audit(u['id'],'create_user','user',str(cur.lastrowid),username+' '+','.join(grants))
    except sqlite3.IntegrityError:raise HTTPException(409,'اسم المستخدم مستخدم مسبقاً.')
    return RedirectResponse('/admin/users',303)

@app.post('/admin/users/{user_id}/update')
def user_update(request:Request,user_id:int,display_name:str=Form(...),role:str=Form(...),permissions:str=Form(''),password:str=Form(''),active:str=Form('0'),permission_flags:list[str]=Form([]),csrf_token:str=Form(...),u=Depends(auth)):
    allow(u,'users');check_csrf(request,csrf_token)
    if role not in PERMISSIONS and role!='custom':raise HTTPException(400,'دور غير صالح')
    grants=list(PERMISSIONS[role]) if role in PERMISSIONS else [p for p in (permission_flags or permissions.split(',')) if p in ('view','upload','edit','delete','users')]
    if user_id==u['id'] and (active!='1' or 'users' not in grants):raise HTTPException(400,'لا يمكن تعطيل صلاحية إدارة حسابك أو إيقافه.')
    with sql() as db:
        old=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
        if not old:raise HTTPException(404,'المستخدم غير موجود')
        if 'users' in old['permissions'].split(',') and ('users' not in grants or active!='1'):
            managers=db.execute("SELECT COUNT(*) FROM users WHERE active=1 AND instr(','||permissions||',', ',users,')>0").fetchone()[0]
            if managers<=1:raise HTTPException(400,'يجب الإبقاء على مدير واحد للنظام على الأقل.')
        if password and len(password)<12:raise HTTPException(400,'كلمة المرور يجب أن تحتوي على 12 حرفاً على الأقل.')
        db.execute('UPDATE users SET display_name=?,role=?,permissions=?,active=? WHERE id=?',(display_name,role,','.join(grants),active=='1',user_id))
        if password:db.execute('UPDATE users SET password_hash=?,failed_attempts=0,locked_until=0 WHERE id=?',(hash_pw(password),user_id))
    audit(u['id'],'update_user','user',str(user_id),'perms='+','.join(grants)+',active='+active+',pw_changed='+str(bool(password)))
    return RedirectResponse('/admin/users',303)

@app.get('/admin/audit',response_class=HTMLResponse)
def audit_page(request:Request,u=Depends(auth)):
    allow(u,'users')
    with sql() as db:
        logs=[dict(x) for x in db.execute('SELECT a.*,u.username FROM audits a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 300')]
    return page(request,'audit.html',logs=logs)

REPORT_INPUTS={
  '1':[('pdf','branch_pdf','تقرير PDF لنشاط الفروع'),('targets','targets','المستهدف السنوي (اختياري، تستخدم قيم الصورة المرجعية عند عدم إرفاقه)')],
  '2':[('payments','payments','حركات السداد'),('contracts','contracts','بيانات العقود الإضافية'),('classification','classification','جدول التصنيف الأساسي')],
  '3':[('current','portfolio','محفظة الشهر الحالي'),('previous','portfolio','محفظة الشهر السابق (اختيارية للحركات)'),('rescheduling','rescheduling','تقرير الجدولة (اختياري)'),('payments','payments','حركات السداد لنفس الفترة (اختياري لإثبات السداد)')],
}

def assemble_report(which,ids,food_exclusion=True):
    types={k:t for k,t,_ in REPORT_INPUTS[which]}
    data={k:load_data(v,types[k]) for k,v in ids.items() if v and k in types}
    files={k:get_upload(v,types[k]) for k,v in ids.items() if v and k in types}
    if which=='1':
        if 'pdf' not in data:raise ValueError('اختر ملف تقرير PDF أولاً.')
        if 'targets' in files and files['pdf']['reference_period'][:4]!=files['targets']['reference_period'][:4]:
            raise ValueError('سنة المستهدف السنوي تختلف عن سنة تقرير PDF. اختر مصادر متطابقة.')
        return report1(data['pdf'],data.get('targets'),include_food=not food_exclusion),files
    if which=='2':
        for k in ('payments','contracts','classification'):
            if k not in data:raise ValueError('يجب تحديد ملفات السداد والعقود والتصنيف جميعاً.')
        return report2(data['payments'],data['contracts'],data['classification']),files
    if 'current' not in data:raise ValueError('حدد ملف المحفظة الحالية.')
    if 'previous' in files:
        if files['previous']['id']==files['current']['id']:raise ValueError('يجب استخدام ملفين مختلفين للمقارنة.')
        if files['previous']['reference_period']>=files['current']['reference_period']:
            raise ValueError('يجب أن تكون فترة المحفظة السابقة أقدم من الحالية.')
    matched=False
    if 'payments' in files:
        if files['payments']['reference_period']!=files['current']['reference_period']:
            raise ValueError('فترة حركات السداد لا تطابق فترة المحفظة الحالية. لا يجوز دمج الفترتين.')
        matched=True
    return report3(data['current'],data.get('previous'),data.get('rescheduling'),data.get('payments'),payments_aligned=matched),files

@app.get('/reports/{which}',response_class=HTMLResponse)
def report_page(which:str,request:Request,u=Depends(auth)):
    allow(u,'view')
    if which not in REPORT_INPUTS:raise HTTPException(404,'تقرير غير موجود')
    inputs=REPORT_INPUTS[which]
    options={key:list_uploads(kind) for key,kind,_ in inputs}
    selected={key:request.query_params.get(key,'') for key,_,_ in inputs}
    is_submit=request.query_params.get('run')=='1';error=None;report=None;used={}
    if is_submit:
        try:
            report,used=assemble_report(which,selected,food_exclusion=request.query_params.get('food','exclude')!='include')
        except (ValueError,HTTPException) as ex:error=ex.detail if isinstance(ex,HTTPException) else str(ex)
    return page(request,'report.html',which=which,inputs=inputs,options=options,selected=selected,
                report=report,used=used,error=error,is_submit=is_submit,
                food=request.query_params.get('food','exclude'))

EXPORT_FIELDS={
'1':[('branch','الفرع'),('target','المستهدف السنوي'),('actual','المحصل الفعلي'),('food','محصل الأمن الغذائي'),('net','التحصيل المحتسب'),('gap','الفجوة'),('achievement','نسبة التحقيق %')],
'2':[('sector','التصنيف الأساسي'),('transactions','حركات السداد'),('contracts','العقود'),('fees','المسدد رسوم'),('principal','المسدد الأساسي مع الفائض'),('total','إجمالي السداد'),('percentage','النسبة %')],
'3':[('contract','رقم العقد'),('branch','الفرع'),('status','الحالة'),('events','الحركات'),('old_balance','الرصيد السابق'),('current_balance','الرصيد الحالي'),('old_due','المستحق السابق'),('current_due','المستحق الحالي'),('writeoff','الشطب'),('schedule_amount','المبلغ المجدول'),('transfer_amount','المبلغ المنقول'),('cash_matched','سداد في ملف الحركات'),('partial_paid','سداد جزئي مؤكد'),('full_paid','سداد كلي مؤكد'),('due_paid','سداد الأقساط'),('unexplained','انخفاض غير مفسر')],
}

@app.get('/export/{which}.xlsx')
def export_excel(which:str,request:Request,u=Depends(auth)):
    allow(u,'view')
    if which not in REPORT_INPUTS:raise HTTPException(404)
    ids={k:request.query_params.get(k,'') for k,_,_ in REPORT_INPUTS[which]}
    try:report,used=assemble_report(which,ids,food_exclusion=request.query_params.get('food','exclude')!='include')
    except ValueError as e:raise HTTPException(400,str(e))
    import xlsxwriter
    out=io.BytesIO(); workbook=xlsxwriter.Workbook(out,{'in_memory':True})
    ws=workbook.add_worksheet('التقرير');ws.right_to_left()
    head=workbook.add_format({'bold':True,'font_color':'white','bg_color':'#123E34','align':'center','border':1})
    num=workbook.add_format({'num_format':'#,##0.00','align':'center'});regular=workbook.add_format({'align':'right'})
    fields=EXPORT_FIELDS[which];rows=report['rows']
    for i,(_,v) in enumerate(fields):ws.write(0,i,v,head);ws.set_column(i,i,22 if i<2 else 19)
    for i,row in enumerate(rows,1):
        for j,(field,_) in enumerate(fields):
            val=row.get(field)
            if isinstance(val,(int,float)):ws.write_number(i,j,val,num)
            else:ws.write_string(i,j,str(val) if val is not None else '',regular)
    ws.freeze_panes(1,0);ws.autofilter(0,0,max(1,len(rows)),len(fields)-1)
    meta=workbook.add_worksheet('مصادر البيانات');meta.right_to_left();meta.write_row(0,0,['النوع','اسم الملف','الفترة المرجعية','تاريخ الرفع'],head)
    for i,(k,up) in enumerate(used.items(),1):meta.write_row(i,0,[k,up['original_name'],up['reference_period'],up['uploaded_at']])
    meta.set_column(0,0,24);meta.set_column(1,1,58);meta.set_column(2,3,24)
    if which=='2':
        det=workbook.add_worksheet('تفاصيل السداد');det.right_to_left();keyz=[('contract','رقم العقد'),('loan_type','نوع القرض'),('sector','القطاع'),('fees','الرسوم'),('principal','الأصل مع الفائض'),('total','المجموع')]
        for j,(_,h) in enumerate(keyz):det.write(0,j,h,head);det.set_column(j,j,25)
        for i,row in enumerate(report['details'],1):
            for j,(name,_) in enumerate(keyz):
                value=row.get(name,'');
                if isinstance(value,(int,float)):det.write_number(i,j,value,num)
                else:det.write_string(i,j,str(value),regular)
        det.freeze_panes(1,0)
    workbook.close();out.seek(0)
    audit(u['id'],'export',f'report{which}',None,','.join(up['id'] for up in used.values()))
    filename=f'ADF_RPT_0{which}_{datetime.now().strftime("%Y%m%d")}.xlsx'
    return StreamingResponse(out,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{filename}"'})
