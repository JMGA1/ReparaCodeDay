"""Repara: servidor local con SQLite, cuentas administrativas por ciudad y archivos estáticos."""
import base64, hashlib, hmac, io, json, math, os, re, secrets, smtplib, sqlite3, ssl, threading, urllib.request, urllib.error
from datetime import datetime, timezone
from email.message import EmailMessage
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image, ImageOps, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data')); DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'ciudad.sqlite3'; PUBLIC = ROOT / 'public'
DATABASE_URL=os.environ.get('DATABASE_URL','').strip(); USE_POSTGRES=DATABASE_URL.startswith(('postgres://','postgresql://'))
if USE_POSTGRES:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError('DATABASE_URL está configurada pero falta psycopg. Ejecutá pip install psycopg[binary].') from exc
CATEGORIES = ['Baches', 'Basura', 'Pérdidas de agua', 'Alumbrado', 'Otros']
PRIORITIES = ['Baja', 'Media', 'Alta', 'Urgente']
CITIES = ['Rivera', 'Santana do Livramento']; STATES = ['Recibido', 'En revisión', 'En proceso', 'Resuelto', 'Rechazado', 'Duplicado']
PUBLIC_STATES = ('En revisión', 'En proceso', 'Resuelto')
MAX_BODY = 4 * 1024 * 1024; Image.MAX_IMAGE_PIXELS = 30_000_000
EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
CENTERS = {'Rivera': (-30.905, -55.55), 'Santana do Livramento': (-30.89, -55.535)}
DEFAULT_ASSIGNEES = {
    'Rivera': ['Sin asignar', 'Cuadrilla de mantenimiento', 'Alumbrado público', 'Limpieza urbana', 'Saneamiento / agua'],
    'Santana do Livramento': ['Sin asignar', 'Equipe de manutenção', 'Iluminação pública', 'Limpeza urbana', 'Saneamento / água']
}

class PgResult:
    def __init__(self, cur): self.cur=cur; self.rowcount=cur.rowcount
    def fetchone(self): return self.cur.fetchone()
    def fetchall(self): return self.cur.fetchall()
    def __iter__(self): return iter(self.cur)

class PgConnection:
    def __init__(self): self.con=psycopg.connect(DATABASE_URL, row_factory=dict_row)
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb):
        if exc_type: self.con.rollback()
        else: self.con.commit()
        self.con.close()
    def execute(self, sql, params=()):
        sql=sql.replace(' COLLATE NOCASE','').replace('?', '%s')
        cur=self.con.execute(sql, params)
        return PgResult(cur)
    def executescript(self, script):
        for stmt in script.split(';'):
            if stmt.strip(): self.con.execute(stmt)
    def commit(self): self.con.commit()
    def rollback(self): self.con.rollback()

def connection():
    if USE_POSTGRES: return PgConnection()
    con=sqlite3.connect(DB, timeout=10); con.row_factory=sqlite3.Row; con.execute('PRAGMA foreign_keys=ON'); return con

def exec_ignore(con, sql, params=()):
    if USE_POSTGRES:
        sql=sql.replace('INSERT OR IGNORE INTO','INSERT INTO').replace('?', '%s')
        if 'ON CONFLICT' not in sql.upper(): sql=sql.rstrip().rstrip(';')+' ON CONFLICT DO NOTHING'
        return PgResult(con.con.execute(sql, params))
    return con.execute(sql,params)

def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 210_000)
    return salt.hex(), digest.hex()

def verify_password(password, salt_hex, digest_hex):
    _, got = password_hash(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(got, digest_hex)

def columns(con, table):
    if USE_POSTGRES:
        return {r['column_name'] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",(table,))}
    return {r['name'] for r in con.execute(f'PRAGMA table_info({table})')}

def public_code(city, ident, created_at=None):
    year=(created_at or datetime.now(timezone.utc).isoformat())[:4]
    prefix='RIV' if city=='Rivera' else 'SLV'
    return f'{prefix}-{year}-{ident:05d}'

def init():
    with connection() as con:
        if USE_POSTGRES:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS incidents (
                id BIGSERIAL PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, public_code TEXT UNIQUE,
                title TEXT NOT NULL, category TEXT NOT NULL, city TEXT NOT NULL,
                address TEXT NOT NULL, description TEXT NOT NULL,
                lat DOUBLE PRECISION NOT NULL, lng DOUBLE PRECISION NOT NULL, status TEXT NOT NULL DEFAULT 'Recibido',
                assignee TEXT NOT NULL DEFAULT 'Sin asignar', created_at TEXT NOT NULL,
                demo INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1, photo BYTEA,
                summary TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'Media',
                priority_reason TEXT NOT NULL DEFAULT '', ai_source TEXT NOT NULL DEFAULT 'fallback', duplicate_of BIGINT
            );
            CREATE TABLE IF NOT EXISTS history (id BIGSERIAL PRIMARY KEY, incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, at TEXT NOT NULL, message TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS confirmations (incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, device TEXT NOT NULL, PRIMARY KEY (incident_id, device));
            CREATE TABLE IF NOT EXISTS subscriptions (incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, email TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (incident_id,email));
            CREATE TABLE IF NOT EXISTS admin_users (id BIGSERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_salt TEXT NOT NULL, password_hash TEXT NOT NULL, city TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assignees (id BIGSERIAL PRIMARY KEY, city TEXT NOT NULL, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE(city,name));
            CREATE TABLE IF NOT EXISTS admin_sessions (token TEXT PRIMARY KEY, username TEXT NOT NULL, city TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_incidents_city_status ON incidents(city,status);
            CREATE INDEX IF NOT EXISTS idx_incidents_created_at ON incidents(created_at);
            """)
        else:
            con.execute('PRAGMA journal_mode=WAL')
            con.executescript("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL, public_code TEXT UNIQUE,
                title TEXT NOT NULL, category TEXT NOT NULL, city TEXT NOT NULL,
                address TEXT NOT NULL, description TEXT NOT NULL,
                lat REAL NOT NULL, lng REAL NOT NULL, status TEXT NOT NULL DEFAULT 'Recibido',
                assignee TEXT NOT NULL DEFAULT 'Sin asignar', created_at TEXT NOT NULL,
                demo INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1, photo BLOB,
                summary TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'Media',
                priority_reason TEXT NOT NULL DEFAULT '', ai_source TEXT NOT NULL DEFAULT 'fallback', duplicate_of INTEGER
            );
            CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, at TEXT NOT NULL, message TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS confirmations (incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, device TEXT NOT NULL, PRIMARY KEY (incident_id, device));
            CREATE TABLE IF NOT EXISTS subscriptions (incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, email TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL, PRIMARY KEY (incident_id,email));
            CREATE TABLE IF NOT EXISTS admin_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL COLLATE NOCASE, password_salt TEXT NOT NULL, password_hash TEXT NOT NULL, city TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assignees (id INTEGER PRIMARY KEY AUTOINCREMENT, city TEXT NOT NULL, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE(city,name));
            CREATE TABLE IF NOT EXISTS admin_sessions (token TEXT PRIMARY KEY, username TEXT NOT NULL, city TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            if 'public_code' not in columns(con,'incidents'):
                con.execute('ALTER TABLE incidents ADD COLUMN public_code TEXT')
                con.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_public_code ON incidents(public_code)')
            migrations={'summary':"TEXT NOT NULL DEFAULT ''",'priority':"TEXT NOT NULL DEFAULT 'Media'",'priority_reason':"TEXT NOT NULL DEFAULT ''",'ai_source':"TEXT NOT NULL DEFAULT 'fallback'",'duplicate_of':'INTEGER'}
            existing=columns(con,'incidents')
            for col,ddl in migrations.items():
                if col not in existing: con.execute(f'ALTER TABLE incidents ADD COLUMN {col} {ddl}')
        for row in con.execute("SELECT id,city,created_at FROM incidents WHERE public_code IS NULL OR public_code=''").fetchall():
            con.execute('UPDATE incidents SET public_code=? WHERE id=?',(public_code(row['city'],row['id'],row['created_at']),row['id']))
        for city,names in DEFAULT_ASSIGNEES.items():
            for name in names: exec_ignore(con,'INSERT OR IGNORE INTO assignees(city,name) VALUES (?,?)',(city,name))
        if os.environ.get('DEMO_MODE','0')=='1':
            demo_password=os.environ.get('DEMO_ADMIN_PASSWORD','CiudadVisible2026!')
            demo_users=[(os.environ.get('DEMO_RIVERA_USER','rivera'),'Rivera'),(os.environ.get('DEMO_LIVRAMENTO_USER','livramento'),'Santana do Livramento')]
            for username,city in demo_users:
                exists=con.execute('SELECT 1 FROM admin_users WHERE LOWER(username)=LOWER(?)',(username,)).fetchone()
                if not exists:
                    salt,digest=password_hash(demo_password)
                    con.execute('INSERT INTO admin_users(username,password_salt,password_hash,city,created_at) VALUES (?,?,?,?,?)',(username,salt,digest,city,now()))
    print(('Modo presentación activo. Cuentas demo disponibles para Rivera y Livramento.' if os.environ.get('DEMO_MODE','0')=='1' else 'Panel privado: cree cuentas con python admin.py user add <usuario> <ciudad>.'),flush=True)
    print(('Base de datos: PostgreSQL.' if USE_POSTGRES else f'Base de datos: SQLite ({DB}).'),flush=True)

def now(): return datetime.now(timezone.utc).isoformat()
def text(value, maximum, required=True):
    if not isinstance(value,str) or len(value.strip())>maximum or (required and not value.strip()): raise ValueError('Hay un campo vacío o demasiado largo.')
    return value.strip()
def valid_email(value):
    email=text(value,254)
    if not EMAIL_RE.match(email): raise ValueError('Ingresá un email válido.')
    return email.lower()
def photo_bytes(value):
    if not value:return None
    if not isinstance(value,str) or not value.startswith('data:image/') or ',' not in value: raise ValueError('Formato de imagen inválido.')
    try:
        raw=base64.b64decode(value.split(',',1)[1], validate=True)
        if len(raw)>3*1024*1024: raise ValueError('La foto es demasiado grande.')
        with Image.open(io.BytesIO(raw)) as im:
            im=ImageOps.exif_transpose(im).convert('RGB'); im.thumbnail((1600,1600)); out=io.BytesIO(); im.save(out,'JPEG',quality=82); return out.getvalue()
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning) as exc: raise ValueError('No se pudo leer la foto. Usá JPG, PNG o WebP.') from exc

def haversine(lat1,lng1,lat2,lng2):
    rad=math.pi/180; a=math.sin((lat2-lat1)*rad/2)**2+math.cos(lat1*rad)*math.cos(lat2*rad)*math.sin((lng2-lng1)*rad/2)**2
    return 6371000*2*math.atan2(math.sqrt(a),math.sqrt(1-a))
def infer_city(lat,lng): return min(CENTERS, key=lambda c:haversine(lat,lng,*CENTERS[c]))
def infer_category(description):
    s=description.lower()
    rules=[('Pérdidas de agua',('agua','caño','cano','vazamento','fuga','saneamiento','esgoto')),('Alumbrado',('luz','foco','luminaria','poste','alumbrado','iluminação','iluminacao','lampada','lâmpada')),('Basura',('basura','residuo','resíduo','lixo','contenedor','contenedor')),('Baches',('bache','pozo','buraco','asfalto','pavimento'))]
    return next((cat for cat,words in rules if any(w in s for w in words)),'Otros')
def infer_title(description,category):
    clean=' '.join(description.split())
    if len(clean)<=72:return clean
    return clean[:69].rstrip()+'…'


def normalized_words(value):
    return {w for w in re.findall(r'[a-záéíóúãõâêôçñ]{3,}', value.lower()) if w not in {'para','pero','como','esta','este','essa','esse','uma','uno','una','que','con','com','del','las','los','por','muy','mais','muito'}}

def fallback_priority(description, confirmations=1):
    s=description.lower()
    urgent=('cable caído','cable electrico','cable eléctrico','fio caído','fio eletrico','incendio','fuego','semáforo apagado','semaforo apagado','árbol caído','arbol caido','árvore caída','bloquea la calle','bloqueia a rua','riesgo inmediato','risco imediato')
    high=('semáforo','semaforo','tránsito','transito','escola','escuela','hospital','avenida','inundación','alagamento')
    if any(x in s for x in urgent): return 'Urgente','Posible riesgo inmediato o bloqueo detectado en la descripción.'
    if any(x in s for x in high) or confirmations>=5: return 'Alta','Afectación relevante de movilidad/servicios o múltiples aportes ciudadanos.'
    if confirmations>=2: return 'Media','Problema confirmado por más de una persona.'
    return 'Media','Prioridad inicial sugerida por tipo de incidencia; requiere validación administrativa.'

def nearby_candidates(con, lat, lng, limit=8):
    rows=con.execute(SELECT+" WHERE demo=0 AND status IN ('En revisión','En proceso') ORDER BY id DESC LIMIT 200").fetchall()
    out=[]
    for r in rows:
        d=haversine(lat,lng,r['lat'],r['lng'])
        if d<=250:
            item=serialize(con,r); item['distance_m']=round(d)
            out.append(item)
    return sorted(out,key=lambda x:x['distance_m'])[:limit]

def fallback_analysis(description, candidates):
    cat=infer_category(description); title=infer_title(description,cat)
    summary=' '.join(description.split())[:260]
    priority,reason=fallback_priority(description)
    words=normalized_words(description); best=None; best_score=0
    for c in candidates:
        other=normalized_words((c.get('title') or '')+' '+(c.get('description') or ''))
        lexical=len(words & other)/max(1,len(words | other))
        proximity=max(0,1-c['distance_m']/250)
        score=.68*lexical+.32*proximity
        if c.get('category')==cat: score+=.12
        if score>best_score: best,best_score=c,score
    return {'category':cat,'title':title,'summary':summary,'priority':priority,'priority_reason':reason,
            'duplicate_id':best['id'] if best and best_score>=.58 else None,
            'duplicate_confidence':min(.95,round(best_score,2)) if best else 0.0,'source':'fallback'}

def ai_enabled(): return bool(os.environ.get('OPENAI_API_KEY'))

def openai_analysis(description, candidates, photo_data=None):
    if not ai_enabled(): return fallback_analysis(description,candidates)
    compact=[{'id':c['id'],'code':c['public_code'],'category':c['category'],'title':c['title'],'description':c['description'],'distance_m':c['distance_m'],'confirmations':c['confirmations']} for c in candidates]
    schema={
      'type':'object','additionalProperties':False,
      'properties':{
        'category':{'type':'string','enum':CATEGORIES},'title':{'type':'string','maxLength':90},
        'summary':{'type':'string','maxLength':320},'priority':{'type':'string','enum':PRIORITIES},
        'priority_reason':{'type':'string','maxLength':320},
        'duplicate_id':{'type':['integer','null']},'duplicate_confidence':{'type':'number','minimum':0,'maximum':1}},
      'required':['category','title','summary','priority','priority_reason','duplicate_id','duplicate_confidence']}
    prompt=("Analiza un reporte ciudadano urbano de Rivera (Uruguay) o Santana do Livramento (Brasil). "
            "Devuelve JSON según el esquema. Clasifica categoría, crea un título breve y un resumen neutral. "
            "Sugiere prioridad considerando riesgo, impacto, bloqueo de tránsito/servicios y evidencia, sin exagerar. "
            "Marca duplicate_id SOLO si uno de los candidatos describe claramente el mismo problema físico en el mismo lugar; "
            "si hay duda usa null. No inventes hechos. Reporte: "+description+"\nCandidatos cercanos: "+json.dumps(compact,ensure_ascii=False))
    content=[{'type':'input_text','text':prompt+'\nResponde únicamente JSON.'}]
    if photo_data: content.append({'type':'input_image','image_url':photo_data,'detail':'low'})
    payload={'model':os.environ.get('OPENAI_MODEL','gpt-5-nano'),'input':[{'role':'user','content':content}],
             'text':{'format':{'type':'json_schema','name':'incident_analysis','strict':True,'schema':schema}}}
    req=urllib.request.Request('https://api.openai.com/v1/responses',data=json.dumps(payload).encode(),
        headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY'],'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=35) as res: data=json.load(res)
        text_out=data.get('output_text')
        if not text_out:
            for item in data.get('output',[]):
                for c in item.get('content',[]):
                    if c.get('type')=='output_text': text_out=c.get('text'); break
        result=json.loads(text_out)
        valid_ids={c['id'] for c in candidates}
        if result.get('duplicate_id') not in valid_ids: result['duplicate_id']=None; result['duplicate_confidence']=0
        if result.get('category') not in CATEGORIES or result.get('priority') not in PRIORITIES: raise ValueError('IA devolvió valores inválidos')
        result['source']='openai'; return result
    except Exception as exc:
        print(f'IA no disponible; usando fallback: {exc}',flush=True)
        return fallback_analysis(description,candidates)

def serialize(con,row):
    r=dict(row); r.pop('request_id',None); r['photo_url']=f"/api/incidents/{r['id']}/photo" if r.pop('has_photo',False) else None; r['demo']=bool(r['demo'])
    r['confirmations']=con.execute('SELECT count(*) AS total FROM confirmations WHERE incident_id=?',(r['id'],)).fetchone()['total']
    r['history']=[dict(h) for h in con.execute('SELECT at,message FROM history WHERE incident_id=? ORDER BY id',(r['id'],))]; return r
SELECT='SELECT id,public_code,title,category,city,address,description,lat,lng,status,assignee,created_at,demo,revision,summary,priority,priority_reason,ai_source,duplicate_of,(photo IS NOT NULL) AS has_photo FROM incidents'

def smtp_enabled(): return bool(os.environ.get('SMTP_HOST') and os.environ.get('SMTP_FROM'))
def send_status_email(email, incident, note=''):
    if not smtp_enabled(): return False
    host=os.environ['SMTP_HOST']; port=int(os.environ.get('SMTP_PORT','587')); user=os.environ.get('SMTP_USER',''); password=os.environ.get('SMTP_PASSWORD',''); use_tls=os.environ.get('SMTP_TLS','1')!='0'
    msg=EmailMessage(); msg['From']=os.environ['SMTP_FROM']; msg['To']=email; msg['Subject']=f"Repara · {incident['public_code']} · {incident['status']}"
    msg.set_content(f"La incidencia {incident['public_code']} cambió a: {incident['status']}.\nResponsable: {incident['assignee']}.\n{note}\n\nEste mensaje fue enviado porque solicitaste actualizaciones de esta incidencia.")
    with smtplib.SMTP(host,port,timeout=15) as server:
        if use_tls: server.starttls(context=ssl.create_default_context())
        if user: server.login(user,password)
        server.send_message(msg)
    return True

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(PUBLIC),**kwargs)
    def setup(self): super().setup(); self.connection.settimeout(20)
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Referrer-Policy','strict-origin-when-cross-origin'); self.send_header('Permissions-Policy','geolocation=(self), camera=(self)'); self.send_header('Cache-Control','no-store' if self.path.startswith('/api/') else 'no-cache'); super().end_headers()
    def send_json(self,data,status=200):
        raw=json.dumps(data,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def session(self):
        auth=self.headers.get('Authorization',''); token=auth[7:] if auth.startswith('Bearer ') else ''
        if not token:return None
        with connection() as con:
            row=con.execute('SELECT username,city FROM admin_sessions WHERE token=?',(token,)).fetchone()
            return dict(row) if row else None
    def require_session(self):
        s=self.session()
        if not s:self.send_json({'error':'Iniciá sesión con tu cuenta administrativa.'},401)
        return s
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/api/health':return self.send_json({'ok':True})
        if path=='/api/config':return self.send_json({'tiles':os.environ.get('TILE_URL','https://tile.openstreetmap.org/{z}/{x}/{y}.png'),'email_notifications':smtp_enabled(),'ai_enabled':True,'ai_mode':'openai' if ai_enabled() else 'local','ai_model':os.environ.get('OPENAI_MODEL','gpt-5-nano') if ai_enabled() else 'motor-local'})
        if path=='/api/session':
            s=self.session(); return self.send_json({'ok':True,'user':s},200) if s else self.send_json({'ok':False},401)
        if path=='/api/incidents':
            with connection() as con:return self.send_json([serialize(con,r) for r in con.execute(SELECT+" WHERE status IN ('En revisión','En proceso','Resuelto') ORDER BY id DESC")])
        if path=='/api/admin/incidents':
            s=self.require_session()
            if not s:return
            with connection() as con:return self.send_json([serialize(con,r) for r in con.execute(SELECT+' WHERE city=? ORDER BY id DESC',(s['city'],))])
        if path=='/api/admin/assignees':
            s=self.require_session()
            if not s:return
            with connection() as con:return self.send_json([r['name'] for r in con.execute('SELECT name FROM assignees WHERE city=? AND active=1 ORDER BY id',(s['city'],))])
        parts=path.strip('/').split('/')
        if len(parts)==4 and parts[:2]==['api','incidents'] and parts[2].isdigit() and parts[3]=='photo':
            ident=int(parts[2])
            with connection() as con:
                row=con.execute('SELECT photo,status,city FROM incidents WHERE id=?',(ident,)).fetchone()
                if not row or not row['photo']: return self.send_json({'error':'Foto no encontrada.'},404)
                # Los reportes sin moderar nunca exponen su foto al público.
                if row['status'] not in PUBLIC_STATES:
                    sess=self.session()
                    if not sess or sess['city']!=row['city']: return self.send_json({'error':'Foto no disponible públicamente.'},404)
                blob=row['photo']
            self.send_response(200); self.send_header('Content-Type','image/jpeg'); self.send_header('Content-Length',str(len(blob))); self.end_headers(); return self.wfile.write(blob)
        if path.startswith('/api/'):return self.send_json({'error':'Ruta no encontrada.'},404)
        return super().do_GET()
    def list_directory(self,path): self.send_error(404); return None
    def do_POST(self): self.mutate()
    def do_PATCH(self): self.mutate()
    def mutate(self):
        try:
            origin=self.headers.get('Origin')
            if origin and urlsplit(origin).netloc!=self.headers.get('Host'):return self.send_json({'error':'Origen no permitido.'},403)
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send_json({'error':'Se requiere JSON.'},415)
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=MAX_BODY:return self.send_json({'error':'El envío está vacío o excede 4 MB.'},413)
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict):raise ValueError('Solicitud inválida.')
            path=urlsplit(self.path).path
            if path=='/api/login' and self.command=='POST':return self.login(body)
            if path=='/api/logout' and self.command=='POST':return self.logout()
            if path=='/api/incidents' and self.command=='POST':return self.create(body)
            if path=='/api/demo' and self.command=='POST':
                s=self.require_session(); return self.seed(s['city']) if s else None
            parts=path.strip('/').split('/')
            if len(parts) not in (3,4) or parts[:2]!=['api','incidents'] or not parts[2].isdigit():return self.send_json({'error':'Ruta no encontrada.'},404)
            ident=int(parts[2])
            with connection() as con:
                row=con.execute('SELECT * FROM incidents WHERE id=?',(ident,)).fetchone()
                if not row:return self.send_json({'error':'Incidencia no encontrada.'},404)
                if len(parts)==4 and parts[3]=='confirm' and self.command=='POST':
                    if row['status'] not in PUBLIC_STATES:return self.send_json({'error':'Esta incidencia todavía no está disponible públicamente.'},404)
                    device=text(body.get('device'),100); cur=exec_ignore(con,'INSERT OR IGNORE INTO confirmations VALUES (?,?)',(ident,device))
                    if cur.rowcount:
                        msg='Un vecino solicita revisar el cierre.' if row['status']=='Resuelto' else 'Un vecino indicó “También vi este problema”.'; con.execute('INSERT INTO history(incident_id,at,message) VALUES (?,?,?)',(ident,now(),msg))
                    con.commit(); return self.send_json({'ok':True,'added':bool(cur.rowcount),'confirmations':con.execute('SELECT count(*) AS total FROM confirmations WHERE incident_id=?',(ident,)).fetchone()['total']})
                if len(parts)==4 and parts[3]=='subscribe' and self.command=='POST':
                    email=valid_email(body.get('email')); cur=exec_ignore(con,'INSERT OR IGNORE INTO subscriptions(incident_id,email,created_at) VALUES (?,?,?)',(ident,email,now())); con.commit()
                    return self.send_json({'ok':True,'added':bool(cur.rowcount),'notifications_enabled':smtp_enabled()})
                if len(parts)==3 and self.command=='PATCH':
                    s=self.require_session()
                    if not s:return
                    if row['city']!=s['city']:return self.send_json({'error':'Tu cuenta no tiene acceso administrativo a esta ciudad.'},403)
                    status=body.get('status')
                    if status not in STATES:raise ValueError('Estado inválido.')
                    priority=body.get('priority',row['priority'])
                    if priority not in PRIORITIES:raise ValueError('Prioridad inválida.')
                    note=text(body.get('note'),1000); assignee=text(body.get('assignee'),100)
                    allowed={r['name'] for r in con.execute('SELECT name FROM assignees WHERE city=? AND active=1',(s['city'],))}
                    if assignee not in allowed:raise ValueError('Seleccioná un responsable válido.')
                    cur=con.execute('UPDATE incidents SET status=?,assignee=?,priority=?,revision=revision+1 WHERE id=? AND revision=?',(status,assignee,priority,ident,body.get('revision')))
                    if not cur.rowcount:return self.send_json({'error':'Otra persona actualizó esta incidencia. Cerrá y volvé a abrir el detalle antes de guardar.'},409)
                    con.execute('INSERT INTO history(incident_id,at,message) VALUES (?,?,?)',(ident,now(),f'{status} · {assignee} · Prioridad {priority}: {note}')); subscribers=[r['email'] for r in con.execute('SELECT email FROM subscriptions WHERE incident_id=?',(ident,))]; updated=dict(con.execute('SELECT public_code,status,assignee FROM incidents WHERE id=?',(ident,)).fetchone()); con.commit()
                    for email in subscribers:
                        try: send_status_email(email,updated,note)
                        except Exception as exc: print(f'No se pudo enviar email a {email}: {exc}',flush=True)
                    return self.send_json({'ok':True})
            return self.send_json({'error':'Ruta no encontrada.'},404)
        except (ValueError,TypeError,KeyError) as exc:return self.send_json({'error':str(exc) or 'Datos inválidos.'},400)
        except Exception:
            import traceback; traceback.print_exc(); return self.send_json({'error':'No se pudo guardar. Intentá nuevamente.'},500)
    def login(self,body):
        username=text(body.get('username'),80); password=text(body.get('password'),200)
        with connection() as con:row=con.execute('SELECT * FROM admin_users WHERE LOWER(username)=LOWER(?) AND active=1',(username,)).fetchone()
        if not row or not verify_password(password,row['password_salt'],row['password_hash']):return self.send_json({'error':'Usuario o contraseña incorrectos.'},401)
        token=secrets.token_urlsafe(32); user={'username':row['username'],'city':row['city']}
        with connection() as con: con.execute('INSERT INTO admin_sessions(token,username,city,created_at) VALUES (?,?,?,?)',(token,user['username'],user['city'],now()))
        return self.send_json({'token':token,'user':user})
    def logout(self):
        auth=self.headers.get('Authorization',''); token=auth[7:] if auth.startswith('Bearer ') else ''
        if token:
            with connection() as con: con.execute('DELETE FROM admin_sessions WHERE token=?',(token,))
        return self.send_json({'ok':True})
    def create(self,body):
        description=text(body.get('description'),1500)
        lat,lng=float(body.get('lat','nan')),float(body.get('lng','nan'))
        if not math.isfinite(lat) or not math.isfinite(lng) or not(-31.05<=lat<=-30.78 and -55.72<=lng<=-55.40):raise ValueError('El punto debe estar en la zona de Rivera–Livramento.')
        city=infer_city(lat,lng); address='Ubicación marcada en el mapa'
        rid=text(body.get('request_id'),100); device=text(body.get('device'),100); photo_value=body.get('photo'); photo=photo_bytes(photo_value)
        email=body.get('email','').strip() if isinstance(body.get('email',''),str) else ''
        if email: email=valid_email(email)
        with connection() as con:
            old=con.execute('SELECT id,public_code FROM incidents WHERE request_id=?',(rid,)).fetchone()
            if old:return self.send_json({'id':old['id'],'code':old['public_code']},200)
            candidates=nearby_candidates(con,lat,lng)
        analysis=openai_analysis(description,candidates,photo_value if isinstance(photo_value,str) else None)
        duplicate=next((c for c in candidates if c['id']==analysis.get('duplicate_id')),None)
        if duplicate and float(analysis.get('duplicate_confidence') or 0)>=0.68 and not body.get('force_new'):
            return self.send_json({'error':'possible_duplicate','message':'Encontramos una incidencia que podría ser el mismo problema.','duplicate':duplicate,'analysis':analysis},409)
        category=analysis['category']; title=text(analysis['title'],90); summary=text(analysis['summary'],320,False); priority=analysis['priority']; reason=text(analysis['priority_reason'],320,False)
        with connection() as con:
            con.execute('BEGIN IMMEDIATE'); old=con.execute('SELECT id,public_code FROM incidents WHERE request_id=?',(rid,)).fetchone()
            if old:return self.send_json({'id':old['id'],'code':old['public_code']},200)
            params=(rid,title,category,city,address,description,lat,lng,now(),photo,summary,priority,reason,analysis['source'],analysis.get('duplicate_id') if body.get('force_new') else None)
            if USE_POSTGRES:
                ident=con.execute('INSERT INTO incidents(request_id,public_code,title,category,city,address,description,lat,lng,created_at,photo,summary,priority,priority_reason,ai_source,duplicate_of) VALUES (?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id',params).fetchone()['id']
            else:
                ident=con.execute('INSERT INTO incidents(request_id,public_code,title,category,city,address,description,lat,lng,created_at,photo,summary,priority,priority_reason,ai_source,duplicate_of) VALUES (?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',params).lastrowid
            code=public_code(city,ident); con.execute('UPDATE incidents SET public_code=? WHERE id=?',(code,ident))
            con.execute('INSERT INTO confirmations VALUES (?,?)',(ident,device)); con.execute('INSERT INTO history(incident_id,at,message) VALUES (?,?,?)',(ident,now(),'Reporte recibido. Pendiente de revisión administrativa antes de su publicación.'))
            if email: exec_ignore(con,'INSERT OR IGNORE INTO subscriptions(incident_id,email,created_at) VALUES (?,?,?)',(ident,email,now()))
        return self.send_json({'id':ident,'code':code,'city':city,'category':category,'title':title,'summary':summary,'priority':priority,'priority_reason':reason,'ai_source':analysis['source'],'subscribed':bool(email),'notifications_enabled':smtp_enabled()},201)
    def seed(self,city):
        examples=[('Bache de ejemplo','Baches','Rivera',-30.906,-55.551,'Recibido'),('Residuos de ejemplo','Basura','Rivera',-30.912,-55.541,'En revisión'),('Pérdida de ejemplo','Pérdidas de agua','Rivera',-30.9015,-55.555,'En proceso'),('Iluminación de ejemplo','Alumbrado','Santana do Livramento',-30.886,-55.538,'Recibido'),('Bache reparado de ejemplo','Baches','Santana do Livramento',-30.89,-55.527,'Resuelto'),('Residuos de ejemplo','Basura','Santana do Livramento',-30.88,-55.545,'En proceso')]
        added=0
        with connection() as con:
            for i,(title,cat,item_city,lat,lng,state) in enumerate(examples):
                if item_city!=city:continue
                params=(f'demo-v4-{i}',title,cat,item_city,'Punto ilustrativo, no es un problema real','Registro ficticio para la presentación académica.',lat,lng,state,now())
                if USE_POSTGRES:
                    got=con.execute('INSERT INTO incidents(request_id,public_code,title,category,city,address,description,lat,lng,status,created_at,demo) VALUES (?,NULL,?,?,?,?,?,?,?,?,?,1) ON CONFLICT (request_id) DO NOTHING RETURNING id',params).fetchone(); ident=got['id'] if got else None
                else:
                    cur=con.execute('INSERT OR IGNORE INTO incidents(request_id,public_code,title,category,city,address,description,lat,lng,status,created_at,demo) VALUES (?,NULL,?,?,?,?,?,?,?,?,?,1)',params); ident=cur.lastrowid if cur.rowcount else None
                if ident:
                    added+=1; code=public_code(item_city,ident); con.execute('UPDATE incidents SET public_code=? WHERE id=?',(code,ident)); exec_ignore(con,'INSERT OR IGNORE INTO confirmations VALUES (?,?)',(ident,'demo')); con.execute('INSERT INTO history(incident_id,at,message) VALUES (?,?,?)',(ident,now(),'Ejemplo ficticio cargado desde el panel privado.'))
        return self.send_json({'added':added})

if __name__=='__main__':
    init(); port=int(os.environ.get('PORT','8080')); print(f'Repara disponible en puerto {port}',flush=True); ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
