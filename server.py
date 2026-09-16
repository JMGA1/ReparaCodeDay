"""Repara: servidor local con SQLite, cuentas administrativas por ciudad y archivos estáticos."""

import logging, time, warnings
import base64, hashlib, hmac, io, json, math, os, re, secrets, smtplib, sqlite3, ssl, threading, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from flask import Flask, Response, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from PIL import Image, ImageOps, UnidentifiedImageError
from local_embeddings import similarities

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("repara")
SESSION_HOURS = max(1, min(24, int(os.environ.get("SESSION_HOURS", "12"))))

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "ciudad.sqlite3"
PUBLIC = ROOT / "public"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
if USE_POSTGRES:
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as exc:
        raise RuntimeError(
            "DATABASE_URL está configurada pero falta psycopg. Ejecutá pip install psycopg[binary]."
        ) from exc
CATEGORIES = ["Baches", "Basura", "Pérdidas de agua", "Alumbrado", "Otros"]
PRIORITIES = ["Baja", "Media", "Alta", "Urgente"]
CITIES = ["Rivera", "Santana do Livramento"]
STATES = ["Recibido", "En revisión", "En proceso", "Resuelto", "Rechazado", "Duplicado"]
PUBLIC_STATES = ("En revisión", "En proceso", "Resuelto")
MAX_BODY = 12 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 30_000_000
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
CENTERS = {"Rivera": (-30.905, -55.55), "Santana do Livramento": (-30.89, -55.535)}
DEFAULT_ASSIGNEES = {
    "Rivera": [
        "Sin asignar",
        "Cuadrilla de mantenimiento",
        "Alumbrado público",
        "Limpieza urbana",
        "Saneamiento / agua",
    ],
    "Santana do Livramento": [
        "Sin asignar",
        "Equipe de manutenção",
        "Iluminação pública",
        "Limpeza urbana",
        "Saneamento / água",
    ],
}

SECTOR_BY_CATEGORY = {
    "Rivera": {
        "Baches": "Cuadrilla de mantenimiento",
        "Basura": "Limpieza urbana",
        "Pérdidas de agua": "Saneamiento / agua",
        "Alumbrado": "Alumbrado público",
        "Otros": "Cuadrilla de mantenimiento",
    },
    "Santana do Livramento": {
        "Baches": "Equipe de manutenção",
        "Basura": "Limpeza urbana",
        "Pérdidas de agua": "Saneamento / água",
        "Alumbrado": "Iluminação pública",
        "Otros": "Equipe de manutenção",
    },
}
COMMUNITY_KINDS = [
    "Limpieza comunitaria",
    "Cuidado de espacios públicos",
    "Campaña solidaria",
    "Otro",
    "No aplica",
]


class PgResult:
    def __init__(self, cur):
        self.cur = cur
        self.rowcount = cur.rowcount

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    def __iter__(self):
        return iter(self.cur)


_pool = None
_pool_lock = threading.Lock()


def get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(
                DATABASE_URL,
                min_size=1,
                max_size=int(os.environ.get("DB_POOL_SIZE", "8")),
                timeout=5,
                kwargs={
                    "row_factory": dict_row,
                    "connect_timeout": 5,
                    "options": "-c statement_timeout=10000",
                },
                open=True,
            )
    return _pool


class PgConnection:
    def __init__(self):
        self.context = get_pool().connection()
        self.con = self.context.__enter__()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return self.context.__exit__(exc_type, exc, tb)

    def execute(self, sql, params=()):
        sql = sql.replace(" COLLATE NOCASE", "").replace("?", "%s")
        cur = self.con.execute(sql, params)
        return PgResult(cur)

    def executescript(self, script):
        for stmt in script.split(";"):
            if stmt.strip():
                self.con.execute(stmt)

    def commit(self):
        self.con.commit()

    def rollback(self):
        self.con.rollback()


class SQLiteConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connection():
    if USE_POSTGRES:
        return PgConnection()
    con = sqlite3.connect(DB, timeout=10, factory=SQLiteConnection)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def exec_ignore(con, sql, params=()):
    if USE_POSTGRES:
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO").replace("?", "%s")
        if "ON CONFLICT" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return PgResult(con.con.execute(sql, params))
    return con.execute(sql, params)


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return salt.hex(), digest.hex()


def verify_password(password, salt_hex, digest_hex):
    _, got = password_hash(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(got, digest_hex)


def columns(con, table):
    if USE_POSTGRES:
        return {
            r["column_name"]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
                (table,),
            )
        }
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def public_code(city, ident, created_at=None):
    year = (created_at or datetime.now(timezone.utc).isoformat())[:4]
    prefix = "RIV" if city == "Rivera" else "SLV"
    return f"{prefix}-{year}-{ident:05d}"


def validate_configuration():
    if os.environ.get("DEMO_MODE", "0") == "1":
        public_environment = (
            os.environ.get("APP_ENV") == "production"
            or bool(DATABASE_URL)
            or bool(os.environ.get("RENDER"))
            or bool(os.environ.get("PUBLIC_URL"))
        )
        if public_environment and os.environ.get("ALLOW_PUBLIC_DEMO", "0") != "1":
            raise RuntimeError(
                "La demo pública requiere ALLOW_PUBLIC_DEMO=1 y una contraseña propia."
            )
        password = os.environ.get("DEMO_ADMIN_PASSWORD", "")
        if len(password) < 12 or password == "CiudadVisible2026!":
            raise RuntimeError(
                "Definí una DEMO_ADMIN_PASSWORD propia de al menos 12 caracteres."
            )
        log.warning(
            "MODO DEMO %s ACTIVO: datos de prueba y cuentas de presentación habilitadas.",
            "PÚBLICO EXPLÍCITAMENTE AUTORIZADO" if public_environment else "LOCAL",
        )


def init():
    validate_configuration()
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
                priority_reason TEXT NOT NULL DEFAULT '', ai_source TEXT NOT NULL DEFAULT 'fallback', duplicate_of BIGINT,
                suggested_sector TEXT NOT NULL DEFAULT '', community_suitable INTEGER NOT NULL DEFAULT 0,
                community_kind TEXT NOT NULL DEFAULT 'No aplica', community_title TEXT NOT NULL DEFAULT '', community_reason TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS history (id BIGSERIAL PRIMARY KEY, incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, at TEXT NOT NULL, message TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS confirmations (incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, device TEXT NOT NULL, PRIMARY KEY (incident_id, device));
            CREATE TABLE IF NOT EXISTS subscriptions (incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, email TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (incident_id,email));
            CREATE TABLE IF NOT EXISTS admin_users (id BIGSERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_salt TEXT NOT NULL, password_hash TEXT NOT NULL, city TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assignees (id BIGSERIAL PRIMARY KEY, city TEXT NOT NULL, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE(city,name));
            CREATE TABLE IF NOT EXISTS admin_sessions (token TEXT PRIMARY KEY, username TEXT NOT NULL, city TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS community_actions (id BIGSERIAL PRIMARY KEY, incident_id BIGINT UNIQUE NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, city TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Activa', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS community_interests (action_id BIGINT NOT NULL REFERENCES community_actions(id) ON DELETE CASCADE, device TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(action_id,device));
            CREATE INDEX IF NOT EXISTS idx_incidents_city_status ON incidents(city,status);
            CREATE INDEX IF NOT EXISTS idx_incidents_created_at ON incidents(created_at);
            """)
        else:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL, public_code TEXT UNIQUE,
                title TEXT NOT NULL, category TEXT NOT NULL, city TEXT NOT NULL,
                address TEXT NOT NULL, description TEXT NOT NULL,
                lat REAL NOT NULL, lng REAL NOT NULL, status TEXT NOT NULL DEFAULT 'Recibido',
                assignee TEXT NOT NULL DEFAULT 'Sin asignar', created_at TEXT NOT NULL,
                demo INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1, photo BLOB,
                summary TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'Media',
                priority_reason TEXT NOT NULL DEFAULT '', ai_source TEXT NOT NULL DEFAULT 'fallback', duplicate_of INTEGER,
                suggested_sector TEXT NOT NULL DEFAULT '', community_suitable INTEGER NOT NULL DEFAULT 0,
                community_kind TEXT NOT NULL DEFAULT 'No aplica', community_title TEXT NOT NULL DEFAULT '', community_reason TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, at TEXT NOT NULL, message TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS confirmations (incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, device TEXT NOT NULL, PRIMARY KEY (incident_id, device));
            CREATE TABLE IF NOT EXISTS subscriptions (incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, email TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL, PRIMARY KEY (incident_id,email));
            CREATE TABLE IF NOT EXISTS admin_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL COLLATE NOCASE, password_salt TEXT NOT NULL, password_hash TEXT NOT NULL, city TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assignees (id INTEGER PRIMARY KEY AUTOINCREMENT, city TEXT NOT NULL, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE(city,name));
            CREATE TABLE IF NOT EXISTS admin_sessions (token TEXT PRIMARY KEY, username TEXT NOT NULL, city TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS community_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER UNIQUE NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, city TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Activa', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS community_interests (action_id INTEGER NOT NULL REFERENCES community_actions(id) ON DELETE CASCADE, device TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(action_id,device));
            """)
            if "public_code" not in columns(con, "incidents"):
                con.execute("ALTER TABLE incidents ADD COLUMN public_code TEXT")
                con.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_public_code ON incidents(public_code)"
                )
            migrations = {
                "summary": "TEXT NOT NULL DEFAULT ''",
                "priority": "TEXT NOT NULL DEFAULT 'Media'",
                "priority_reason": "TEXT NOT NULL DEFAULT ''",
                "ai_source": "TEXT NOT NULL DEFAULT 'fallback'",
                "duplicate_of": "INTEGER",
                "suggested_sector": "TEXT NOT NULL DEFAULT ''",
                "community_suitable": "INTEGER NOT NULL DEFAULT 0",
                "community_kind": "TEXT NOT NULL DEFAULT 'No aplica'",
                "community_title": "TEXT NOT NULL DEFAULT ''",
                "community_reason": "TEXT NOT NULL DEFAULT ''",
            }
            existing = columns(con, "incidents")
            for col, ddl in migrations.items():
                if col not in existing:
                    con.execute(f"ALTER TABLE incidents ADD COLUMN {col} {ddl}")
        if "is_owner" not in columns(con, "admin_users"):
            con.execute(
                "ALTER TABLE admin_users ADD COLUMN is_owner INTEGER NOT NULL DEFAULT 0"
            )
        if "photo_path" not in columns(con, "incidents"):
            con.execute("ALTER TABLE incidents ADD COLUMN photo_path TEXT")
        blob_type = "BYTEA" if USE_POSTGRES else "BLOB"
        con.execute(
            f"CREATE TABLE IF NOT EXISTS incident_photos (incident_id BIGINT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, position INTEGER NOT NULL, photo {blob_type}, photo_path TEXT, PRIMARY KEY(incident_id,position))"
        )
        if "expires_at" not in columns(con, "admin_sessions"):
            con.execute("ALTER TABLE admin_sessions ADD COLUMN expires_at TEXT")
            # Tokens anteriores no tenían TTL y se guardaban sin hash: cerrar esas sesiones.
            con.execute("DELETE FROM admin_sessions")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON admin_sessions(expires_at)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires DOUBLE PRECISION NOT NULL)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_limits_expiry ON rate_limits(expires)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_incidents_city_status ON incidents(city,status)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_incident ON history(incident_id,id)"
        )
        # Desactivar cuentas heredadas con la contraseña compartida, incluso al salir del modo demo.
        for user in con.execute(
            "SELECT username,password_salt,password_hash FROM admin_users"
        ).fetchall():
            if verify_password(
                "CiudadVisible2026!", user["password_salt"], user["password_hash"]
            ):
                con.execute(
                    "UPDATE admin_users SET active=0 WHERE username=?",
                    (user["username"],),
                )
                con.execute(
                    "DELETE FROM admin_sessions WHERE username=?", (user["username"],)
                )
                log.warning(
                    "Cuenta con contraseña demo heredada desactivada; restablecer desde admin.py."
                )
        # Migraciones compatibles con bases ya existentes (SQLite o PostgreSQL).
        existing = columns(con, "incidents")
        generic_migrations = {
            "suggested_sector": "TEXT NOT NULL DEFAULT ''",
            "community_suitable": "INTEGER NOT NULL DEFAULT 0",
            "community_kind": "TEXT NOT NULL DEFAULT 'No aplica'",
            "community_title": "TEXT NOT NULL DEFAULT ''",
            "community_reason": "TEXT NOT NULL DEFAULT ''",
        }
        for col, ddl in generic_migrations.items():
            if col not in existing:
                con.execute(f"ALTER TABLE incidents ADD COLUMN {col} {ddl}")
        if USE_POSTGRES:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS community_actions (id BIGSERIAL PRIMARY KEY, incident_id BIGINT UNIQUE NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, city TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Activa', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS community_interests (action_id BIGINT NOT NULL REFERENCES community_actions(id) ON DELETE CASCADE, device TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(action_id,device));
            """)
        else:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS community_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER UNIQUE NOT NULL REFERENCES incidents(id) ON DELETE CASCADE, city TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Activa', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS community_interests (action_id INTEGER NOT NULL REFERENCES community_actions(id) ON DELETE CASCADE, device TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(action_id,device));
            """)
        action_columns = columns(con, "community_actions")
        for name in (
            "activity_details",
            "meeting_point",
            "schedule",
            "organizer",
            "materials",
        ):
            if name not in action_columns:
                con.execute(
                    f"ALTER TABLE community_actions ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                )
        if "revision" not in action_columns:
            con.execute(
                "ALTER TABLE community_actions ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )
        for row in con.execute(
            "SELECT id,city,created_at FROM incidents WHERE public_code IS NULL OR public_code=''"
        ).fetchall():
            con.execute(
                "UPDATE incidents SET public_code=? WHERE id=?",
                (public_code(row["city"], row["id"], row["created_at"]), row["id"]),
            )
        for city, names in DEFAULT_ASSIGNEES.items():
            for name in names:
                exec_ignore(
                    con,
                    "INSERT OR IGNORE INTO assignees(city,name) VALUES (?,?)",
                    (city, name),
                )
        if os.environ.get("DEMO_MODE", "0") == "1":
            demo_password = os.environ["DEMO_ADMIN_PASSWORD"]
            demo_users = [
                (os.environ.get("DEMO_RIVERA_USER", "rivera"), "Rivera"),
                (
                    os.environ.get("DEMO_LIVRAMENTO_USER", "livramento"),
                    "Santana do Livramento",
                ),
            ]
            for username, city in demo_users:
                exists = con.execute(
                    "SELECT 1 FROM admin_users WHERE LOWER(username)=LOWER(?)",
                    (username,),
                ).fetchone()
                if not exists:
                    salt, digest = password_hash(demo_password)
                    con.execute(
                        "INSERT INTO admin_users(username,password_salt,password_hash,city,created_at) VALUES (?,?,?,?,?)",
                        (username, salt, digest, city, now()),
                    )
    log.info(
        "Base de datos inicializada: %s", "PostgreSQL" if USE_POSTGRES else "SQLite"
    )


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value, maximum, required=True):
    if (
        not isinstance(value, str)
        or len(value.strip()) > maximum
        or (required and not value.strip())
    ):
        raise ValueError("Hay un campo vacío o demasiado largo.")
    return value.strip()


def valid_email(value):
    email = text(value, 254)
    if not EMAIL_RE.match(email):
        raise ValueError("Ingresá un email válido.")
    return email.lower()


def photo_bytes(value):
    if not value:
        return None
    if (
        not isinstance(value, str)
        or not value.startswith("data:image/")
        or "," not in value
    ):
        raise ValueError("Formato de imagen inválido.")
    try:
        if len(value) > 4 * 1024 * 1024:
            raise ValueError("La foto es demasiado grande.")
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        if len(raw) > 3 * 1024 * 1024:
            raise ValueError("La foto es demasiado grande.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as check:
                check.verify()
        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1600, 1600))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=82)
            return out.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("No se pudo leer la foto. Usá JPG, PNG o WebP.") from exc


def haversine(lat1, lng1, lat2, lng2):
    rad = math.pi / 180
    a = (
        math.sin((lat2 - lat1) * rad / 2) ** 2
        + math.cos(lat1 * rad)
        * math.cos(lat2 * rad)
        * math.sin((lng2 - lng1) * rad / 2) ** 2
    )
    return 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def infer_city(lat, lng):
    return min(CENTERS, key=lambda c: haversine(lat, lng, *CENTERS[c]))


def infer_category(description):
    s = description.lower()
    rules = [
        (
            "Pérdidas de agua",
            ("agua", "caño", "cano", "vazamento", "fuga", "saneamiento", "esgoto"),
        ),
        (
            "Alumbrado",
            (
                "luz",
                "foco",
                "luminaria",
                "poste",
                "alumbrado",
                "iluminação",
                "iluminacao",
                "lampada",
                "lâmpada",
            ),
        ),
        (
            "Basura",
            ("basura", "residuo", "resíduo", "lixo", "contenedor", "contenedor"),
        ),
        ("Baches", ("bache", "pozo", "buraco", "asfalto", "pavimento")),
    ]
    return next((cat for cat, words in rules if any(w in s for w in words)), "Otros")


def infer_title(description, category):
    clean = " ".join(description.split())
    if len(clean) <= 72:
        return clean
    return clean[:69].rstrip() + "…"


def normalized_words(value):
    return {
        w
        for w in re.findall(r"[a-záéíóúãõâêôçñ]{3,}", value.lower())
        if w
        not in {
            "para",
            "pero",
            "como",
            "esta",
            "este",
            "essa",
            "esse",
            "uma",
            "uno",
            "una",
            "que",
            "con",
            "com",
            "del",
            "las",
            "los",
            "por",
            "muy",
            "mais",
            "muito",
        }
    }


def fallback_priority(description, confirmations=1):
    s = description.lower()
    urgent = (
        "cable caído",
        "cable electrico",
        "cable eléctrico",
        "fio caído",
        "fio eletrico",
        "incendio",
        "fuego",
        "semáforo apagado",
        "semaforo apagado",
        "árbol caído",
        "arbol caido",
        "árvore caída",
        "bloquea la calle",
        "bloqueia a rua",
        "riesgo inmediato",
        "risco imediato",
    )
    high = (
        "semáforo",
        "semaforo",
        "tránsito",
        "transito",
        "escola",
        "escuela",
        "hospital",
        "avenida",
        "inundación",
        "alagamento",
    )
    if any(x in s for x in urgent):
        return (
            "Urgente",
            "Posible riesgo inmediato o bloqueo detectado en la descripción.",
        )
    if any(x in s for x in high) or confirmations >= 5:
        return (
            "Alta",
            "Afectación relevante de movilidad/servicios o múltiples aportes ciudadanos.",
        )
    if confirmations >= 2:
        return "Media", "Problema confirmado por más de una persona."
    return (
        "Media",
        "Prioridad inicial sugerida por tipo de incidencia; requiere validación administrativa.",
    )


def nearby_candidates(con, lat, lng, limit=8):
    rows = con.execute(
        SELECT
        + " WHERE demo=0 AND status IN ('En revisión','En proceso') ORDER BY id DESC LIMIT 200"
    ).fetchall()
    out = []
    for r in rows:
        d = haversine(lat, lng, r["lat"], r["lng"])
        if d <= 250:
            item = serialize(con, r)
            item["distance_m"] = round(d)
            out.append(item)
    return sorted(out, key=lambda x: x["distance_m"])[:limit]


def community_suggestion(description, category):
    s = description.lower()
    unsafe = (
        "cable",
        "electric",
        "eléctric",
        "fio",
        "incendio",
        "fuego",
        "semáforo",
        "semaforo",
        "tránsito",
        "transito",
        "árbol caído",
        "arbol caido",
        "árvore caída",
        "inundación",
        "alagamento",
        "esgoto",
        "saneamiento",
    )
    if any(x in s for x in unsafe) or category in (
        "Baches",
        "Pérdidas de agua",
        "Alumbrado",
    ):
        return (
            False,
            "No aplica",
            "",
            "Requiere intervención técnica o puede implicar riesgos; no se recomienda voluntariado ciudadano.",
        )
    clean_words = (
        "basura",
        "lixo",
        "residuo",
        "resíduo",
        "plaza",
        "praça",
        "parque",
        "botellas",
        "garrafas",
        "papeles",
        "litter",
        "limpieza",
        "limpeza",
    )
    solidarity = (
        "donación",
        "donacion",
        "doação",
        "doacao",
        "alimentos",
        "ropa",
        "roupa",
        "refugio",
        "abrigo",
        "ayuda comunitaria",
        "ajuda comunitária",
    )
    if any(x in s for x in clean_words) or category == "Basura":
        return (
            True,
            "Limpieza comunitaria",
            "Jornada comunitaria de limpieza",
            "El reporte describe una tarea de bajo riesgo que puede complementar la respuesta pública con participación voluntaria.",
        )
    if any(x in s for x in solidarity):
        return (
            True,
            "Campaña solidaria",
            "Acción solidaria comunitaria",
            "La necesidad descrita puede organizarse como una acción voluntaria con validación administrativa.",
        )
    return (
        False,
        "No aplica",
        "",
        "No hay evidencia suficiente para proponer una acción comunitaria segura.",
    )


def fallback_analysis(description, candidates, city=None):
    cat = infer_category(description)
    title = infer_title(description, cat)
    summary = " ".join(description.split())[:260]
    priority, reason = fallback_priority(description)
    community, kind, community_title, community_reason = community_suggestion(
        description, cat
    )
    sector = SECTOR_BY_CATEGORY.get(city or "Rivera", {}).get(cat, "") if city else ""
    words = normalized_words(description)
    best = None
    best_score = 0
    semantic = similarities(description, candidates)
    for index, c in enumerate(candidates):
        other = normalized_words(
            (c.get("title") or "") + " " + (c.get("description") or "")
        )
        lexical = len(words & other) / max(1, len(words | other))
        proximity = max(0, 1 - c["distance_m"] / 250)
        # Semántica opcional conservadora, combinada con distancia y categoría.
        similarity = (
            lexical
            if semantic is None
            else max(lexical, max(0, (semantic[index] - 0.45) / 0.55))
        )
        score = 0.68 * similarity + 0.32 * proximity
        if c.get("category") == cat:
            score += 0.12
        if score > best_score:
            best, best_score = c, score
    return {
        "category": cat,
        "title": title,
        "summary": summary,
        "priority": priority,
        "priority_reason": reason,
        "suggested_sector": sector,
        "community_suitable": community,
        "community_kind": kind,
        "community_title": community_title,
        "community_reason": community_reason,
        "duplicate_id": best["id"] if best and best_score >= 0.58 else None,
        "duplicate_confidence": min(0.95, round(best_score, 2)) if best else 0.0,
        "source": "fallback",
    }


def ai_enabled():
    return bool(os.environ.get("OPENAI_API_KEY"))


def _openai_analysis(description, candidates, city, photo_data=None):
    if not ai_enabled():
        return fallback_analysis(description, candidates, city)
    compact = [
        {
            "id": c["id"],
            "code": c["public_code"],
            "category": c["category"],
            "title": c["title"],
            "description": c["description"],
            "distance_m": c["distance_m"],
            "confirmations": c["confirmations"],
        }
        for c in candidates
    ]
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "category": {"type": "string", "enum": CATEGORIES},
            "title": {"type": "string", "maxLength": 90},
            "summary": {"type": "string", "maxLength": 320},
            "priority": {"type": "string", "enum": PRIORITIES},
            "priority_reason": {"type": "string", "maxLength": 320},
            "suggested_sector": {"type": "string", "maxLength": 100},
            "community_suitable": {"type": "boolean"},
            "community_kind": {"type": "string", "enum": COMMUNITY_KINDS},
            "community_title": {"type": "string", "maxLength": 120},
            "community_reason": {"type": "string", "maxLength": 320},
            "duplicate_id": {"type": ["integer", "null"]},
            "duplicate_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "category",
            "title",
            "summary",
            "priority",
            "priority_reason",
            "suggested_sector",
            "community_suitable",
            "community_kind",
            "community_title",
            "community_reason",
            "duplicate_id",
            "duplicate_confidence",
        ],
    }
    prompt = (
        "Analiza un reporte ciudadano urbano de Rivera (Uruguay) o Santana do Livramento (Brasil). "
        "Devuelve JSON según el esquema. Clasifica categoría, crea un título breve y un resumen neutral. "
        "Sugiere prioridad considerando riesgo, impacto, bloqueo de tránsito/servicios y evidencia, sin exagerar. "
        f"La ciudad es {city}. Recomienda suggested_sector usando uno de estos nombres exactos: {SECTOR_BY_CATEGORY.get(city,{})}. "
        "Evalúa si el caso admite una acción comunitaria voluntaria SEGURA que complemente, no reemplace, la responsabilidad pública. "
        "Nunca sugieras voluntariado para electricidad, tránsito, saneamiento, infraestructura peligrosa, árboles caídos, inundaciones u otros riesgos. "
        "Si es apta, genera community_title, community_kind y una justificación breve; si no, community_suitable=false, kind='No aplica' y título vacío. "
        "Marca duplicate_id SOLO si uno de los candidatos describe claramente el mismo problema físico en el mismo lugar; "
        "si hay duda usa null. No inventes hechos. Reporte: "
        + description
        + "\nCandidatos cercanos: "
        + json.dumps(compact, ensure_ascii=False)
    )
    content = [{"type": "input_text", "text": prompt + "\nResponde únicamente JSON."}]
    if photo_data:
        content.append(
            {"type": "input_image", "image_url": photo_data, "detail": "low"}
        )
    payload = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-5-nano"),
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "incident_analysis",
                "strict": True,
                "schema": schema,
            }
        },
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            req, timeout=float(os.environ.get("AI_TIMEOUT_SECONDS", "8"))
        ) as res:
            data = json.load(res)
        text_out = data.get("output_text")
        if not text_out:
            for item in data.get("output", []):
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        text_out = c.get("text")
                        break
        result = json.loads(text_out)
        valid_ids = {c["id"] for c in candidates}
        if result.get("duplicate_id") not in valid_ids:
            result["duplicate_id"] = None
            result["duplicate_confidence"] = 0
        if (
            result.get("category") not in CATEGORIES
            or result.get("priority") not in PRIORITIES
        ):
            raise ValueError("IA devolvió valores inválidos")
        allowed_sectors = set(DEFAULT_ASSIGNEES.get(city, []))
        if result.get("suggested_sector") not in allowed_sectors:
            result["suggested_sector"] = SECTOR_BY_CATEGORY.get(city, {}).get(
                result["category"], ""
            )
        if result.get("community_kind") not in COMMUNITY_KINDS:
            result["community_kind"] = "No aplica"
        result["source"] = "openai"
        result["_usage"] = data.get("usage", {})
        return result
    except Exception as exc:
        log.warning("IA externa no disponible: %s", type(exc).__name__)
        return fallback_analysis(description, candidates, city)


def serialize(con, row):
    r = dict(row)
    r.pop("request_id", None)
    r["photo_url"] = (
        f"/api/incidents/{r['id']}/photo" if r.pop("has_photo", False) else None
    )
    r["demo"] = bool(r["demo"])
    r["community_suitable"] = bool(r.get("community_suitable"))
    r["photo_urls"] = ([r["photo_url"]] if r["photo_url"] else []) + [
        f"/api/incidents/{r['id']}/photo?index={x['position']}"
        for x in con.execute(
            "SELECT position FROM incident_photos WHERE incident_id=? ORDER BY position",
            (r["id"],),
        )
    ]
    r["confirmations"] = con.execute(
        "SELECT count(*) AS total FROM confirmations WHERE incident_id=?", (r["id"],)
    ).fetchone()["total"]
    action = con.execute(
        "SELECT id,status FROM community_actions WHERE incident_id=?", (r["id"],)
    ).fetchone()
    r["community_action_id"] = action["id"] if action else None
    r["community_action_status"] = action["status"] if action else None
    r["history"] = [
        dict(h)
        for h in con.execute(
            "SELECT at,message FROM history WHERE incident_id=? ORDER BY id", (r["id"],)
        )
    ]
    return r


SELECT = "SELECT id,public_code,title,category,city,address,description,lat,lng,status,assignee,created_at,demo,revision,summary,priority,priority_reason,ai_source,duplicate_of,suggested_sector,community_suitable,community_kind,community_title,community_reason,(photo IS NOT NULL OR photo_path IS NOT NULL) AS has_photo FROM incidents"


def smtp_enabled():
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send_status_email(email, incident, note=""):
    if not smtp_enabled():
        return False
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    use_tls = os.environ.get("SMTP_TLS", "1") != "0"
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = email
    msg["Subject"] = f"Repara · {incident['public_code']} · {incident['status']}"
    msg.set_content(
        f"La incidencia {incident['public_code']} cambió a: {incident['status']}.\nResponsable: {incident['assignee']}.\n{note}\n\nEste mensaje fue enviado porque solicitaste actualizaciones de esta incidencia."
    )
    with smtplib.SMTP(host, port, timeout=15) as server:
        if use_tls:
            server.starttls(context=ssl.create_default_context())
        if user:
            server.login(user, password)
        server.send_message(msg)
    return True


class Handler:
    """Lógica de API ejecutada dentro del contexto de una solicitud Flask."""

    def __init__(self):
        self.headers = request.headers
        self.path = request.full_path
        self.command = request.method

    def send_json(self, data, status=200):
        return Response(
            json.dumps(data, ensure_ascii=False),
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def limited(self, scope, identity, limit, seconds):
        retry = rate_limit(scope, identity, limit, seconds)
        if retry:
            response = self.send_json(
                {
                    "error": "Demasiados intentos. Intentá más tarde.",
                    "retry_after": retry,
                },
                429,
            )
            response.headers["Retry-After"] = str(retry)
            return response
        return None

    def incident_page(self, city=None):
        try:
            page = max(1, int(request.args.get("page", "1")))
            limit = max(1, min(100, int(request.args.get("limit", "50"))))
        except ValueError:
            return self.send_json({"error": "Página o límite inválido."}, 400)
        where = (
            ["city=?"]
            if city
            else ["status IN ('En revisión','En proceso','Resuelto')"]
        )
        params = [city] if city else []
        for key in ("city", "category", "status"):
            value = request.args.get(key, "")
            if value:
                where.append(key + "=?")
                params.append(value[:100])
        query = request.args.get("q", "").strip()[:100]
        if query:
            where.append(
                "(LOWER(title) LIKE ? OR LOWER(public_code) LIKE ? OR LOWER(address) LIKE ? OR CAST(id AS TEXT)=?)"
            )
            params.extend(["%" + query.lower() + "%"] * 3 + [query])
        clause = " WHERE " + " AND ".join(where)
        with connection() as con:
            total = con.execute(
                "SELECT count(*) AS n FROM incidents" + clause, params
            ).fetchone()["n"]
            page = min(page, max(1, math.ceil(total / limit)))
            rows = con.execute(
                SELECT + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [limit, (page - 1) * limit],
            ).fetchall()
            items = [serialize(con, row) for row in rows]
        if "page" not in request.args:
            return self.send_json(items)
        return self.send_json(
            {
                "items": items,
                "page": page,
                "limit": limit,
                "total": total,
                "pages": max(1, math.ceil(total / limit)),
            }
        )

    def report_summary(self, city):
        try:
            start = request.args.get("from", "1970-01-01")
            end = request.args.get("to", now()[:10])
            datetime.strptime(start, "%Y-%m-%d")
            datetime.strptime(end, "%Y-%m-%d")
            if start > end:
                raise ValueError()
        except ValueError:
            return self.send_json({"error": "Período inválido."}, 400)
        clause = " WHERE city=? AND substr(created_at,1,10)>=? AND substr(created_at,1,10)<=?"
        params = [city, start, end]
        if request.args.get("examples") == "exclude":
            clause += " AND demo=0"
        with connection() as con:
            categories = {
                row["category"]: row["n"]
                for row in con.execute(
                    "SELECT category,count(*) AS n FROM incidents"
                    + clause
                    + " GROUP BY category",
                    params,
                )
            }
            statuses = {
                row["status"]: row["n"]
                for row in con.execute(
                    "SELECT status,count(*) AS n FROM incidents"
                    + clause
                    + " GROUP BY status",
                    params,
                )
            }
            total = sum(statuses.values())
            urgent = con.execute(
                "SELECT count(*) AS n FROM incidents"
                + clause
                + " AND priority IN ('Alta','Urgente')",
                params,
            ).fetchone()["n"]
            contributions = con.execute(
                "SELECT count(*) AS n FROM confirmations WHERE incident_id IN (SELECT id FROM incidents"
                + clause
                + ")",
                params,
            ).fetchone()["n"]
        return self.send_json(
            {
                "aggregate": True,
                "length": total,
                "solved": statuses.get("Resuelto", 0),
                "urgent": urgent,
                "contributions": contributions,
                "category": categories,
                "status": statuses,
            }
        )

    def session(self):
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token:
            return None
        with connection() as con:
            row = con.execute(
                "SELECT s.username,s.city,u.is_owner FROM admin_sessions s JOIN admin_users u ON u.username=s.username AND u.city=s.city WHERE s.token=? AND s.expires_at>? AND u.active=1",
                (token_hash(token), now()),
            ).fetchone()
            return dict(row) if row else None

    def require_session(self):
        s = self.session()
        return s

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            try:
                with connection() as con:
                    con.execute("SELECT 1").fetchone()
                return self.send_json({"ok": True, "database": "ok"})
            except Exception:
                log.exception("Health: base de datos no disponible")
                return self.send_json({"ok": False, "database": "unavailable"}, 503)
        if path == "/api/owner/users":
            return self.manage_users()
        if path == "/api/admin/metrics":
            if not self.require_session():
                return self.send_json({"error": "Sesión requerida."}, 401)
            with _ai_lock:
                return self.send_json(dict(_ai_metrics))
        if path == "/api/config":
            return self.send_json(
                {
                    "tiles": os.environ.get(
                        "TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
                    ),
                    "email_notifications": smtp_enabled(),
                    "demo_enabled": os.environ.get("DEMO_MODE") == "1",
                    "ai_enabled": True,
                    "ai_mode": "openai" if ai_enabled() else "local",
                    "ai_model": (
                        os.environ.get("OPENAI_MODEL", "gpt-5-nano")
                        if ai_enabled()
                        else "motor-local"
                    ),
                }
            )
        if path == "/api/session":
            s = self.session()
            return (
                self.send_json({"ok": True, "user": s}, 200)
                if s
                else self.send_json({"ok": False}, 401)
            )
        if path == "/api/community-actions":
            with connection() as con:
                rows = con.execute(
                    "SELECT ca.*,i.public_code,i.lat,i.lng,i.address,i.category,i.title AS incident_title,i.description AS incident_description,(i.photo IS NOT NULL OR i.photo_path IS NOT NULL) AS has_photo,(SELECT count(*) FROM community_interests ci WHERE ci.action_id=ca.id) AS volunteers FROM community_actions ca JOIN incidents i ON i.id=ca.incident_id WHERE ca.status='Activa' AND i.status IN ('En revisión','En proceso','Resuelto') ORDER BY ca.id DESC"
                ).fetchall()
                return self.send_json([dict(r) for r in rows])
        if path == "/api/incidents":
            return self.incident_page()
        if path == "/api/admin/incidents":
            s = self.require_session()
            if not s:
                return self.send_json(
                    {"error": "Iniciá sesión con tu cuenta administrativa."}, 401
                )
            return self.incident_page(s["city"])
        if path == "/api/admin/report":
            sess = self.require_session()
            if not sess:
                return self.send_json({"error": "Sesión requerida."}, 401)
            return self.report_summary(sess["city"])
        if path == "/api/admin/assignees":
            s = self.require_session()
            if not s:
                return self.send_json(
                    {"error": "Iniciá sesión con tu cuenta administrativa."}, 401
                )
            with connection() as con:
                return self.send_json(
                    [
                        r["name"]
                        for r in con.execute(
                            "SELECT name FROM assignees WHERE city=? AND active=1 ORDER BY id",
                            (s["city"],),
                        )
                    ]
                )
        parts = path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[:2] == ["api", "incidents"]
            and parts[2].isdigit()
            and parts[3] == "photo"
        ):
            ident = int(parts[2])
            with connection() as con:
                row = con.execute(
                    "SELECT photo,photo_path,status,city FROM incidents WHERE id=?",
                    (ident,),
                ).fetchone()
                if not row:
                    return self.send_json({"error": "Foto no encontrada."}, 404)
                # Los reportes sin moderar nunca exponen su foto al público.
                if row["status"] not in PUBLIC_STATES:
                    sess = self.session()
                    if not sess or sess["city"] != row["city"]:
                        return self.send_json(
                            {"error": "Foto no disponible públicamente."}, 404
                        )
                try:
                    position = int(request.args.get("index", "0"))
                except ValueError:
                    return self.send_json({"error": "Índice inválido."}, 400)
                photo_row = (
                    row
                    if position == 0
                    else con.execute(
                        "SELECT photo,photo_path FROM incident_photos WHERE incident_id=? AND position=?",
                        (ident, position),
                    ).fetchone()
                )
                if not photo_row:
                    return self.send_json({"error": "Foto no encontrada."}, 404)
                blob = read_photo(photo_row)
                if not blob:
                    return self.send_json({"error": "Foto no encontrada."}, 404)
            return Response(bytes(blob), content_type="image/jpeg")
        if path.startswith("/api/"):
            return self.send_json({"error": "Ruta no encontrada."}, 404)
        return self.send_json({"error": "Ruta no encontrada."}, 404)

    def do_POST(self):
        self.mutate()

    def do_PATCH(self):
        self.mutate()

    def mutate(self):
        try:
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self.send_json({"error": "Origen no permitido."}, 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send_json({"error": "Se requiere JSON."}, 415)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                return self.send_json(
                    {"error": "El envío está vacío o excede 12 MB."}, 413
                )
            body = json.loads(request.get_data())
            if not isinstance(body, dict):
                raise ValueError("Solicitud inválida.")
            path = urlsplit(self.path).path
            if path == "/api/owner/users" and self.command == "POST":
                return self.manage_users(body)
            if path == "/api/login" and self.command == "POST":
                return self.login(body)
            if path == "/api/admin/incidents/bulk" and self.command == "POST":
                return self.bulk_update(body)
            if path == "/api/logout" and self.command == "POST":
                return self.logout()
            if self.command == "POST" and path != "/api/login":
                limited = self.limited(
                    "write-ip", request.remote_addr or "unknown", 60, 600
                )
                if limited is not None:
                    return limited
            if path == "/api/incidents" and self.command == "POST":
                limited = self.limited(
                    "incident-ip",
                    request.remote_addr or "unknown",
                    int(os.environ.get("INCIDENT_RATE_LIMIT", "10")),
                    600,
                )
                if limited is not None:
                    return limited
                return self.create(body)
            if (
                path.startswith("/api/community-actions/")
                and path.endswith("/interest")
                and self.command == "POST"
            ):
                parts = path.strip("/").split("/")
                action_id = (
                    int(parts[2]) if len(parts) == 4 and parts[2].isdigit() else 0
                )
                device = text(body.get("device"), 100)
                with connection() as con:
                    action = con.execute(
                        "SELECT ca.id FROM community_actions ca JOIN incidents i ON i.id=ca.incident_id WHERE ca.id=? AND ca.status='Activa' AND i.status IN ('En revisión','En proceso','Resuelto')",
                        (action_id,),
                    ).fetchone()
                    if not action:
                        return self.send_json(
                            {"error": "Acción comunitaria no disponible."}, 404
                        )
                    cur = exec_ignore(
                        con,
                        "INSERT OR IGNORE INTO community_interests(action_id,device,created_at) VALUES (?,?,?)",
                        (action_id, device, now()),
                    )
                    con.commit()
                    total = con.execute(
                        "SELECT count(*) AS total FROM community_interests WHERE action_id=?",
                        (action_id,),
                    ).fetchone()["total"]
                    return self.send_json(
                        {"ok": True, "added": bool(cur.rowcount), "volunteers": total}
                    )
            if path == "/api/demo" and self.command == "POST":
                s = self.require_session()
                if not s:
                    return self.send_json({"error": "Sesión requerida."}, 401)
                if os.environ.get("DEMO_MODE") != "1":
                    return self.send_json({"error": "Modo demo desactivado."}, 403)
                return self.seed(s["city"])
            parts = path.strip("/").split("/")
            if (
                len(parts) not in (3, 4)
                or parts[:2] != ["api", "incidents"]
                or not parts[2].isdigit()
            ):
                return self.send_json({"error": "Ruta no encontrada."}, 404)
            ident = int(parts[2])
            with connection() as con:
                row = con.execute(
                    "SELECT * FROM incidents WHERE id=?", (ident,)
                ).fetchone()
                if not row:
                    return self.send_json({"error": "Incidencia no encontrada."}, 404)
                if (
                    len(parts) == 4
                    and parts[3] == "community-action"
                    and self.command == "POST"
                ):
                    s = self.require_session()
                    if not s:
                        return self.send_json(
                            {"error": "Iniciá sesión con tu cuenta administrativa."},
                            401,
                        )
                    if row["city"] != s["city"]:
                        return self.send_json(
                            {
                                "error": "Tu cuenta no tiene acceso administrativo a esta ciudad."
                            },
                            403,
                        )
                    if not bool(row["community_suitable"]):
                        return self.send_json(
                            {
                                "error": "La IA no marcó esta incidencia como apta para una acción comunitaria."
                            },
                            400,
                        )
                    if row["status"] not in PUBLIC_STATES:
                        return self.send_json(
                            {
                                "error": "Aprobá y publicá la incidencia antes de crear la acción comunitaria."
                            },
                            400,
                        )
                    title = text(
                        body.get("title")
                        or row["community_title"]
                        or ("Acción comunitaria · " + row["title"]),
                        120,
                    )
                    kind = text(row["community_kind"] or "Otro", 80)
                    description = text(
                        row["community_reason"] or row["summary"] or row["description"],
                        500,
                    )
                    details = {
                        key: text(body.get(key, ""), limit, False)
                        for key, limit in [
                            ("activity_details", 1500),
                            ("meeting_point", 250),
                            ("schedule", 150),
                            ("organizer", 150),
                            ("materials", 500),
                        ]
                    }
                    if body and not details["activity_details"]:
                        raise ValueError(
                            "Describí la actividad concreta que se realizará."
                        )
                    previous = con.execute(
                        "SELECT id,revision FROM community_actions WHERE incident_id=?",
                        (ident,),
                    ).fetchone()
                    if previous and not body:
                        return self.send_json({"ok": True, "action_id": previous["id"]})
                    if previous and body.get("revision") != previous["revision"]:
                        return self.send_json(
                            {
                                "error": "La actividad cambió. Volvé a abrirla antes de guardar."
                            },
                            409,
                        )
                    if USE_POSTGRES:
                        got = con.execute(
                            "INSERT INTO community_actions(incident_id,city,title,kind,description,created_at) VALUES (?,?,?,?,?,?) ON CONFLICT (incident_id) DO NOTHING RETURNING id",
                            (ident, row["city"], title, kind, description, now()),
                        ).fetchone()
                        action_id = (
                            got["id"]
                            if got
                            else con.execute(
                                "SELECT id FROM community_actions WHERE incident_id=?",
                                (ident,),
                            ).fetchone()["id"]
                        )
                    else:
                        exec_ignore(
                            con,
                            "INSERT OR IGNORE INTO community_actions(incident_id,city,title,kind,description,created_at) VALUES (?,?,?,?,?,?)",
                            (ident, row["city"], title, kind, description, now()),
                        )
                        action_id = con.execute(
                            "SELECT id FROM community_actions WHERE incident_id=?",
                            (ident,),
                        ).fetchone()["id"]
                    expected = previous["revision"] if previous else 1
                    changed = con.execute(
                        "UPDATE community_actions SET title=?,activity_details=?,meeting_point=?,schedule=?,organizer=?,materials=?,revision=revision+1 WHERE id=? AND revision=?",
                        (
                            title,
                            details["activity_details"],
                            details["meeting_point"],
                            details["schedule"],
                            details["organizer"],
                            details["materials"],
                            action_id,
                            expected,
                        ),
                    )
                    if not changed.rowcount:
                        con.rollback()
                        return self.send_json(
                            {
                                "error": "La actividad cambió. Volvé a abrirla antes de guardar."
                            },
                            409,
                        )
                    con.execute(
                        "INSERT INTO history(incident_id,at,message) VALUES (?,?,?)",
                        (
                            ident,
                            now(),
                            "Acción comunitaria validada por administración a partir de una sugerencia de IA.",
                        ),
                    )
                    con.commit()
                    return self.send_json({"ok": True, "action_id": action_id})
                if len(parts) == 4 and parts[3] == "confirm" and self.command == "POST":
                    if row["status"] not in PUBLIC_STATES:
                        return self.send_json(
                            {
                                "error": "Esta incidencia todavía no está disponible públicamente."
                            },
                            404,
                        )
                    device = text(body.get("device"), 100)
                    cur = exec_ignore(
                        con,
                        "INSERT OR IGNORE INTO confirmations VALUES (?,?)",
                        (ident, device),
                    )
                    if cur.rowcount:
                        msg = (
                            "Un vecino solicita revisar el cierre."
                            if row["status"] == "Resuelto"
                            else "Un vecino indicó “También vi este problema”."
                        )
                        con.execute(
                            "INSERT INTO history(incident_id,at,message) VALUES (?,?,?)",
                            (ident, now(), msg),
                        )
                    con.commit()
                    return self.send_json(
                        {
                            "ok": True,
                            "added": bool(cur.rowcount),
                            "confirmations": con.execute(
                                "SELECT count(*) AS total FROM confirmations WHERE incident_id=?",
                                (ident,),
                            ).fetchone()["total"],
                        }
                    )
                if (
                    len(parts) == 4
                    and parts[3] == "subscribe"
                    and self.command == "POST"
                ):
                    email = valid_email(body.get("email"))
                    cur = exec_ignore(
                        con,
                        "INSERT OR IGNORE INTO subscriptions(incident_id,email,created_at) VALUES (?,?,?)",
                        (ident, email, now()),
                    )
                    con.commit()
                    return self.send_json(
                        {
                            "ok": True,
                            "added": bool(cur.rowcount),
                            "notifications_enabled": smtp_enabled(),
                        }
                    )
                if len(parts) == 3 and self.command == "PATCH":
                    s = self.require_session()
                    if not s:
                        return self.send_json(
                            {"error": "Iniciá sesión con tu cuenta administrativa."},
                            401,
                        )
                    if row["city"] != s["city"]:
                        return self.send_json(
                            {
                                "error": "Tu cuenta no tiene acceso administrativo a esta ciudad."
                            },
                            403,
                        )
                    status = body.get("status")
                    if status not in STATES:
                        raise ValueError("Estado inválido.")
                    priority = body.get("priority", row["priority"])
                    if priority not in PRIORITIES:
                        raise ValueError("Prioridad inválida.")
                    note = text(body.get("note"), 1000)
                    assignee = text(body.get("assignee"), 100)
                    allowed = {
                        r["name"]
                        for r in con.execute(
                            "SELECT name FROM assignees WHERE city=? AND active=1",
                            (s["city"],),
                        )
                    }
                    if assignee not in allowed:
                        raise ValueError("Seleccioná un responsable válido.")
                    cur = con.execute(
                        "UPDATE incidents SET status=?,assignee=?,priority=?,revision=revision+1 WHERE id=? AND revision=?",
                        (status, assignee, priority, ident, body.get("revision")),
                    )
                    if not cur.rowcount:
                        return self.send_json(
                            {
                                "error": "Otra persona actualizó esta incidencia. Cerrá y volvé a abrir el detalle antes de guardar."
                            },
                            409,
                        )
                    con.execute(
                        "INSERT INTO history(incident_id,at,message) VALUES (?,?,?)",
                        (
                            ident,
                            now(),
                            f"{status} · {assignee} · Prioridad {priority}: {note}",
                        ),
                    )
                    subscribers = [
                        r["email"]
                        for r in con.execute(
                            "SELECT email FROM subscriptions WHERE incident_id=?",
                            (ident,),
                        )
                    ]
                    updated = dict(
                        con.execute(
                            "SELECT public_code,status,assignee FROM incidents WHERE id=?",
                            (ident,),
                        ).fetchone()
                    )
                    con.commit()
                    for email in subscribers:
                        try:
                            send_status_email(email, updated, note)
                        except Exception:
                            log.exception(
                                "No se pudo enviar una notificación de estado"
                            )
                    return self.send_json({"ok": True})
            return self.send_json({"error": "Ruta no encontrada."}, 404)
        except (ValueError, TypeError, KeyError) as exc:
            return self.send_json({"error": str(exc) or "Datos inválidos."}, 400)
        except Exception:
            log.exception("Error guardando solicitud")
            return self.send_json(
                {"error": "No se pudo guardar. Intentá nuevamente."}, 500
            )

    def manage_users(self, body=None):
        sess = self.session()
        if not sess:
            return self.send_json({"error": "Sesión requerida."}, 401)
        if not sess.get("is_owner"):
            return self.send_json(
                {"error": "Solo el propietario puede gestionar cuentas."}, 403
            )
        if body is None:
            with connection() as con:
                return self.send_json(
                    [
                        dict(row)
                        for row in con.execute(
                            "SELECT username,city,active,is_owner FROM admin_users ORDER BY city,username"
                        )
                    ]
                )
        limited = self.limited("owner-users", sess["username"], 20, 900)
        if limited is not None:
            return limited
        username = text(body.get("username"), 80).lower()
        if not re.fullmatch(r"[a-z0-9_.-]{3,80}", username):
            raise ValueError(
                "Usá entre 3 y 80 letras, números, punto, guión o guión bajo."
            )
        action = body.get("action")
        if action not in ("create", "reset", "disable"):
            raise ValueError("Acción inválida.")
        password = body.get("password", "")
        if action != "disable":
            password = text(password, 200)
            if len(password) < 12 or password == "CiudadVisible2026!":
                raise ValueError("Usá una contraseña propia de al menos 12 caracteres.")
        with connection() as con:
            if not USE_POSTGRES:
                con.execute("BEGIN IMMEDIATE")
            target = con.execute(
                "SELECT username,is_owner FROM admin_users WHERE LOWER(username)=LOWER(?)",
                (username,),
            ).fetchone()
            if target and target["is_owner"]:
                return self.send_json(
                    {"error": "Las cuentas propietarias se gestionan desde admin.py."},
                    403,
                )
            if action == "create":
                if target:
                    return self.send_json({"error": "El usuario ya existe."}, 409)
                city = body.get("city")
                if city not in CITIES:
                    raise ValueError("Ciudad inválida.")
                salt, digest = password_hash(password)
                con.execute(
                    "INSERT INTO admin_users(username,password_salt,password_hash,city,created_at) VALUES (?,?,?,?,?)",
                    (username, salt, digest, city, now()),
                )
            else:
                if not target:
                    return self.send_json({"error": "Usuario no encontrado."}, 404)
                username = target["username"]
                if action == "reset":
                    salt, digest = password_hash(password)
                    con.execute(
                        "UPDATE admin_users SET password_salt=?,password_hash=?,active=1 WHERE username=?",
                        (salt, digest, username),
                    )
                else:
                    con.execute(
                        "UPDATE admin_users SET active=0 WHERE username=?", (username,)
                    )
                con.execute("DELETE FROM admin_sessions WHERE username=?", (username,))
        log.info("Gestión de cuenta completada: action=%s", action)
        return self.send_json({"ok": True})

    def bulk_update(self, body):
        sess = self.require_session()
        if not sess:
            return self.send_json({"error": "Sesión requerida."}, 401)
        items = body.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 50:
            raise ValueError("Seleccioná entre 1 y 50 incidencias.")
        note = text(body.get("note"), 1000)
        status = body.get("status") or None
        assignee = body.get("assignee") or None
        if status is not None and status not in STATES:
            raise ValueError("Estado inválido.")
        if not status and not assignee:
            raise ValueError("Seleccioná estado o responsable.")
        notifications = []
        with connection() as con:
            if not USE_POSTGRES:
                con.execute("BEGIN IMMEDIATE")
            if (
                assignee
                and not con.execute(
                    "SELECT 1 FROM assignees WHERE city=? AND name=? AND active=1",
                    (sess["city"], assignee),
                ).fetchone()
            ):
                raise ValueError("Responsable inválido.")
            ids = set()
            if any(
                not isinstance(item, dict) or type(item.get("id")) is not int
                for item in items
            ):
                raise ValueError("Selección inválida.")
            for item in sorted(items, key=lambda item: item["id"]):
                if (
                    not isinstance(item, dict)
                    or type(item.get("id")) is not int
                    or type(item.get("revision")) is not int
                ):
                    raise ValueError("Selección inválida.")
                if item["id"] in ids:
                    raise ValueError("Selección repetida.")
                ids.add(item["id"])
                row = con.execute(
                    "SELECT id,city,revision,status,assignee,public_code FROM incidents WHERE id=?"
                    + (" FOR UPDATE" if USE_POSTGRES else ""),
                    (item["id"],),
                ).fetchone()
                if not row or row["city"] != sess["city"]:
                    con.rollback()
                    return self.send_json(
                        {"error": "Selección fuera de tu ciudad."}, 403
                    )
                if row["revision"] != item["revision"]:
                    con.rollback()
                    return self.send_json(
                        {
                            "error": "Hay incidencias modificadas. Actualizá la selección."
                        },
                        409,
                    )
                changed = dict(row)
                changed["status"] = status or row["status"]
                changed["assignee"] = assignee or row["assignee"]
                con.execute(
                    "UPDATE incidents SET status=?,assignee=?,revision=revision+1 WHERE id=?",
                    (changed["status"], changed["assignee"], row["id"]),
                )
                con.execute(
                    "INSERT INTO history(incident_id,at,message) VALUES (?,?,?)",
                    (
                        row["id"],
                        now(),
                        f"{changed['status']} · {changed['assignee']}: {note}",
                    ),
                )
                for subscriber in con.execute(
                    "SELECT email FROM subscriptions WHERE incident_id=?", (row["id"],)
                ):
                    notifications.append((subscriber["email"], changed))
        for email, changed in notifications:
            try:
                send_status_email(email, changed, note)
            except Exception:
                log.exception("Error enviando notificación de cambio masivo")
        return self.send_json({"ok": True, "updated": len(items)})

    def login(self, body):
        limited = self.limited("login-ip", request.remote_addr or "unknown", 30, 900)
        if limited is not None:
            return limited
        username = text(body.get("username"), 80).lower()
        password = text(body.get("password"), 200)
        limited = self.limited("login-user", username, 5, 900)
        if limited is not None:
            return limited
        with connection() as con:
            row = con.execute(
                "SELECT * FROM admin_users WHERE LOWER(username)=LOWER(?) AND active=1",
                (username,),
            ).fetchone()
        valid = verify_password(
            password,
            row["password_salt"] if row else "00" * 16,
            row["password_hash"] if row else "00" * 32,
        )
        if not row or not valid:
            return self.send_json({"error": "Usuario o contraseña incorrectos."}, 401)
        with connection() as con:
            con.execute(
                "DELETE FROM rate_limits WHERE key=?",
                (limit_key("login-user", username),),
            )
        token = secrets.token_urlsafe(32)
        user = {
            "username": row["username"],
            "city": row["city"],
            "is_owner": bool(row["is_owner"]),
        }
        with connection() as con:
            con.execute(
                "INSERT INTO admin_sessions(token,username,city,created_at,expires_at) VALUES (?,?,?,?,?)",
                (
                    token_hash(token),
                    user["username"],
                    user["city"],
                    now(),
                    (
                        datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
                    ).isoformat(),
                ),
            )
        return self.send_json({"token": token, "user": user})

    def logout(self):
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if token:
            with connection() as con:
                con.execute(
                    "DELETE FROM admin_sessions WHERE token=?", (token_hash(token),)
                )
        return self.send_json({"ok": True})

    def create(self, body):
        description = text(body.get("description"), 1500)
        lat, lng = float(body.get("lat", "nan")), float(body.get("lng", "nan"))
        if (
            not math.isfinite(lat)
            or not math.isfinite(lng)
            or not (-31.05 <= lat <= -30.78 and -55.72 <= lng <= -55.40)
        ):
            raise ValueError("El punto debe estar en la zona de Rivera–Livramento.")
        city = infer_city(lat, lng)
        address = (
            text(body.get("address", ""), 200, False) or "Ubicación marcada en el mapa"
        )
        rid = text(body.get("request_id"), 100)
        device = text(body.get("device"), 100)
        photo_values = body.get("photos", [body["photo"]] if body.get("photo") else [])
        if not isinstance(photo_values, list) or len(photo_values) > 3:
            raise ValueError("Se admiten hasta 3 fotos.")
        photos = [photo_bytes(value) for value in photo_values]
        if any(value is None for value in photos):
            raise ValueError("Foto vacía.")
        photo = photos[0] if photos else None
        email = (
            body.get("email", "").strip()
            if isinstance(body.get("email", ""), str)
            else ""
        )
        if email:
            email = valid_email(email)
        with connection() as con:
            old = con.execute(
                "SELECT id,public_code FROM incidents WHERE request_id=?", (rid,)
            ).fetchone()
            if old:
                return self.send_json(
                    {"id": old["id"], "code": old["public_code"]}, 200
                )
            candidates = nearby_candidates(con, lat, lng)
        analysis = openai_analysis(
            description,
            candidates,
            city,
            (
                ("data:image/jpeg;base64," + base64.b64encode(photo).decode())
                if photo
                else None
            ),
        )
        duplicate = next(
            (c for c in candidates if c["id"] == analysis.get("duplicate_id")), None
        )
        if (
            duplicate
            and float(analysis.get("duplicate_confidence") or 0) >= 0.68
            and not body.get("force_new")
        ):
            return self.send_json(
                {
                    "error": "possible_duplicate",
                    "message": "Encontramos una incidencia que podría ser el mismo problema.",
                    "duplicate": duplicate,
                    "analysis": analysis,
                },
                409,
            )
        category = analysis["category"]
        title = text(analysis["title"], 90)
        summary = text(analysis["summary"], 320, False)
        priority = analysis["priority"]
        reason = text(analysis["priority_reason"], 320, False)
        suggested_sector = text(analysis.get("suggested_sector", ""), 100, False)
        community_suitable = 1 if analysis.get("community_suitable") else 0
        community_kind = text(analysis.get("community_kind", "No aplica"), 80, False)
        community_title = text(analysis.get("community_title", ""), 120, False)
        community_reason = text(analysis.get("community_reason", ""), 320, False)
        with connection() as con:
            if not USE_POSTGRES:
                con.execute("BEGIN IMMEDIATE")
            old = con.execute(
                "SELECT id,public_code FROM incidents WHERE request_id=?", (rid,)
            ).fetchone()
            if old:
                return self.send_json(
                    {"id": old["id"], "code": old["public_code"]}, 200
                )
            params = (
                rid,
                title,
                category,
                city,
                address,
                description,
                lat,
                lng,
                now(),
                photo,
                summary,
                priority,
                reason,
                analysis["source"],
                analysis.get("duplicate_id") if body.get("force_new") else None,
                suggested_sector,
                community_suitable,
                community_kind,
                community_title,
                community_reason,
            )
            if USE_POSTGRES:
                inserted = con.execute(
                    "INSERT INTO incidents(request_id,public_code,title,category,city,address,description,lat,lng,created_at,photo,summary,priority,priority_reason,ai_source,duplicate_of,suggested_sector,community_suitable,community_kind,community_title,community_reason) VALUES (?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(request_id) DO NOTHING RETURNING id",
                    params,
                ).fetchone()
                if not inserted:
                    old = con.execute(
                        "SELECT id,public_code FROM incidents WHERE request_id=?",
                        (rid,),
                    ).fetchone()
                    return self.send_json(
                        {"id": old["id"], "code": old["public_code"]}, 200
                    )
                ident = inserted["id"]
            else:
                ident = con.execute(
                    "INSERT INTO incidents(request_id,public_code,title,category,city,address,description,lat,lng,created_at,photo,summary,priority,priority_reason,ai_source,duplicate_of,suggested_sector,community_suitable,community_kind,community_title,community_reason) VALUES (?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    params,
                ).lastrowid
            for position, image in enumerate(photos):
                stored = store_photo(image)
                if position == 0 and stored:
                    con.execute(
                        "UPDATE incidents SET photo=NULL,photo_path=? WHERE id=?",
                        (stored, ident),
                    )
                elif position > 0:
                    con.execute(
                        "INSERT INTO incident_photos(incident_id,position,photo,photo_path) VALUES (?,?,?,?)",
                        (ident, position, None if stored else image, stored),
                    )
            code = public_code(city, ident)
            con.execute("UPDATE incidents SET public_code=? WHERE id=?", (code, ident))
            con.execute("INSERT INTO confirmations VALUES (?,?)", (ident, device))
            con.execute(
                "INSERT INTO history(incident_id,at,message) VALUES (?,?,?)",
                (
                    ident,
                    now(),
                    "Reporte recibido. Pendiente de revisión administrativa antes de su publicación.",
                ),
            )
            if email:
                exec_ignore(
                    con,
                    "INSERT OR IGNORE INTO subscriptions(incident_id,email,created_at) VALUES (?,?,?)",
                    (ident, email, now()),
                )
        return self.send_json(
            {
                "id": ident,
                "code": code,
                "city": city,
                "category": category,
                "title": title,
                "summary": summary,
                "priority": priority,
                "priority_reason": reason,
                "ai_source": analysis["source"],
                "suggested_sector": suggested_sector,
                "community_suitable": bool(community_suitable),
                "community_kind": community_kind,
                "community_title": community_title,
                "community_reason": community_reason,
                "subscribed": bool(email),
                "notifications_enabled": smtp_enabled(),
            },
            201,
        )

    def seed(self, city):
        examples = [
            ("Bache de ejemplo", "Baches", "Rivera", -30.906, -55.551, "Recibido"),
            (
                "Residuos de ejemplo",
                "Basura",
                "Rivera",
                -30.912,
                -55.541,
                "En revisión",
            ),
            (
                "Pérdida de ejemplo",
                "Pérdidas de agua",
                "Rivera",
                -30.9015,
                -55.555,
                "En proceso",
            ),
            (
                "Iluminación de ejemplo",
                "Alumbrado",
                "Santana do Livramento",
                -30.886,
                -55.538,
                "Recibido",
            ),
            (
                "Bache reparado de ejemplo",
                "Baches",
                "Santana do Livramento",
                -30.89,
                -55.527,
                "Resuelto",
            ),
            (
                "Residuos de ejemplo",
                "Basura",
                "Santana do Livramento",
                -30.88,
                -55.545,
                "En proceso",
            ),
        ]
        added = 0
        with connection() as con:
            for i, (title, cat, item_city, lat, lng, state) in enumerate(examples):
                if item_city != city:
                    continue
                params = (
                    f"demo-v4-{i}",
                    title,
                    cat,
                    item_city,
                    "Punto ilustrativo, no es un problema real",
                    "Registro ficticio para la presentación académica.",
                    lat,
                    lng,
                    state,
                    now(),
                )
                if USE_POSTGRES:
                    got = con.execute(
                        "INSERT INTO incidents(request_id,public_code,title,category,city,address,description,lat,lng,status,created_at,demo) VALUES (?,NULL,?,?,?,?,?,?,?,?,?,1) ON CONFLICT (request_id) DO NOTHING RETURNING id",
                        params,
                    ).fetchone()
                    ident = got["id"] if got else None
                else:
                    cur = con.execute(
                        "INSERT OR IGNORE INTO incidents(request_id,public_code,title,category,city,address,description,lat,lng,status,created_at,demo) VALUES (?,NULL,?,?,?,?,?,?,?,?,?,1)",
                        params,
                    )
                    ident = cur.lastrowid if cur.rowcount else None
                if ident:
                    added += 1
                    code = public_code(item_city, ident)
                    con.execute(
                        "UPDATE incidents SET public_code=? WHERE id=?", (code, ident)
                    )
                    exec_ignore(
                        con,
                        "INSERT OR IGNORE INTO confirmations VALUES (?,?)",
                        (ident, "demo"),
                    )
                    con.execute(
                        "INSERT INTO history(incident_id,at,message) VALUES (?,?,?)",
                        (
                            ident,
                            now(),
                            "Ejemplo ficticio cargado desde el panel privado.",
                        ),
                    )
        return self.send_json({"added": added})


def store_photo(blob):
    directory = os.environ.get("PHOTOS_DIR", "")
    if not directory:
        return None
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(24) + ".jpg"
    target = root / key
    with target.open("xb") as stream:
        stream.write(blob)
    return key


def read_photo(row):
    if row["photo_path"]:
        root = os.environ.get("PHOTOS_DIR", "")
        key = row["photo_path"]
        if not root or not re.fullmatch(r"[a-f0-9]{48}\.jpg", key):
            log.error("Almacenamiento de fotos no configurado o referencia inválida")
            return None
        try:
            return (Path(root) / key).read_bytes()
        except FileNotFoundError:
            return None
    return row["photo"]


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def limit_key(scope, identity):
    return scope + ":" + hashlib.sha256(identity.encode()).hexdigest()


def rate_limit(scope, identity, limit, seconds):
    """Reserva atómica compartida entre hilos/workers; no confía en device ni XFF."""
    key = limit_key(scope, identity)
    stamp = time.time()
    with connection() as con:
        row = con.execute(
            """INSERT INTO rate_limits(key,hits,expires) VALUES (?,1,?)
            ON CONFLICT(key) DO UPDATE SET
              hits=CASE WHEN rate_limits.expires<=? THEN 1 ELSE rate_limits.hits+1 END,
              expires=CASE WHEN rate_limits.expires<=? THEN ? ELSE rate_limits.expires END
            RETURNING hits,expires""",
            (key, stamp + seconds, stamp, stamp, stamp + seconds),
        ).fetchone()
    return max(1, math.ceil(row["expires"] - stamp)) if row["hits"] > limit else 0


def cleanup_expired():
    with connection() as con:
        con.execute(
            "DELETE FROM admin_sessions WHERE expires_at IS NULL OR expires_at<=?",
            (now(),),
        )
        con.execute("DELETE FROM rate_limits WHERE expires<=?", (time.time(),))


def maintenance():
    while True:
        try:
            cleanup_expired()
        except Exception:
            log.exception("Error en limpieza periódica")
        time.sleep(300)


_ai_lock = threading.Lock()
_ai_state = {"failures": 0, "until": 0.0, "probe": False}
_ai_metrics = {
    "calls": 0,
    "successes": 0,
    "fallbacks": 0,
    "errors": 0,
    "latency_ms": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "estimated_cost_usd": 0.0,
}


def openai_analysis(description, candidates, city, photo_data=None):
    if not ai_enabled():
        with _ai_lock:
            _ai_metrics["fallbacks"] += 1
        return fallback_analysis(description, candidates, city)
    with _ai_lock:
        blocked = time.monotonic() < _ai_state["until"] or _ai_state["probe"]
        if blocked:
            _ai_metrics["fallbacks"] += 1
        else:
            if _ai_state["failures"] >= 3:
                _ai_state["probe"] = True
            _ai_metrics["calls"] += 1
    if blocked:
        return fallback_analysis(description, candidates, city)
    started = time.monotonic()
    result = _openai_analysis(description, candidates, city, photo_data)
    elapsed = round((time.monotonic() - started) * 1000)
    usage = result.pop("_usage", {})
    with _ai_lock:
        _ai_state["probe"] = False
        _ai_metrics["latency_ms"] += elapsed
        if result["source"] == "openai":
            _ai_state.update(failures=0, until=0.0)
            _ai_metrics["successes"] += 1
            for field in ("input_tokens", "output_tokens"):
                _ai_metrics[field] += int(usage.get(field, 0))
            _ai_metrics["estimated_cost_usd"] += (
                int(usage.get("input_tokens", 0))
                * float(os.environ.get("AI_INPUT_USD_PER_MILLION", "0"))
                + int(usage.get("output_tokens", 0))
                * float(os.environ.get("AI_OUTPUT_USD_PER_MILLION", "0"))
            ) / 1_000_000
        else:
            _ai_metrics["errors"] += 1
            _ai_metrics["fallbacks"] += 1
            _ai_state["failures"] += 1
            if _ai_state["failures"] >= 3:
                _ai_state["until"] = time.monotonic() + 180
    log.info(
        "ai source=%s latency_ms=%s input_tokens=%s output_tokens=%s",
        result["source"],
        elapsed,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
    )
    return result


def redact_error_event(event, hint):
    for key in ("request", "user", "breadcrumbs"):
        event.pop(key, None)
    return event


def create_app(initialize=True):
    if initialize:
        init()
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    # Solo activar si el proxy frontal elimina encabezados suministrados por el cliente.
    hops = int(os.environ.get("TRUST_PROXY_HOPS", "0"))
    if hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops)

    @app.after_request
    def security_headers(response):
        tile = urlsplit(
            os.environ.get("TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        )
        tile_origin = (
            f"{tile.scheme}://{tile.netloc}" if tile.scheme in ("http", "https") else ""
        )
        if any(c in tile_origin for c in ";\r\n '"):
            tile_origin = ""
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data: blob: {tile_origin}; connect-src 'self'; "
            "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; worker-src 'self'; manifest-src 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(self), camera=(self)"
        response.headers["Cache-Control"] = (
            "no-store" if request.path.startswith("/api/") else "no-cache"
        )
        return response

    @app.errorhandler(Exception)
    def errors(exc):
        if isinstance(exc, HTTPException):
            return Handler().send_json({"error": exc.description}, exc.code)
        log.exception("Error procesando solicitud")
        return Handler().send_json({"error": "Error interno del servidor."}, 500)

    @app.route("/api/<path:path>", methods=["GET", "POST", "PATCH"])
    def api_route(path):
        handler = Handler()
        return (
            handler.do_GET() if request.method in ("GET", "HEAD") else handler.mutate()
        )

    @app.route("/")
    def index():
        return send_from_directory(PUBLIC, "index.html")

    @app.route("/<path:path>")
    def static_file(path):
        return send_from_directory(PUBLIC, path)

    if os.environ.get("SENTRY_DSN"):
        import sentry_sdk

        sentry_sdk.init(
            dsn=os.environ["SENTRY_DSN"],
            send_default_pii=False,
            traces_sample_rate=0,
            include_local_variables=False,
            before_send=redact_error_event,
        )
    threading.Thread(target=maintenance, name="session-cleanup", daemon=True).start()
    return app


if __name__ == "__main__":
    create_app().run(
        host=(
            "127.0.0.1"
            if os.environ.get("DEMO_MODE") == "1"
            else os.environ.get("HOST", "127.0.0.1")
        ),
        port=int(os.environ.get("PORT", "8080")),
        threaded=True,
        debug=False,
    )
