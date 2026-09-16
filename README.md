# Repara v11.4

Reportes ciudadanos para Rivera y Santana do Livramento. Interfaz ES/PT, moderación por ciudad, mapas, fotos, informes y sugerencias de IA.

## Actualizar desde v10.8

1. Respaldá la base de datos y el volumen persistente antes de reemplazar el código. Conservá `data/`, `.env` y las variables del hosting.
2. En el hosting, cambiá **DEMO_MODE a 0 antes del despliegue**. Para una demostración pública, utilizá la excepción explícita explicada abajo.
3. Instalá las dependencias nuevas o reconstruí la imagen Docker.
4. El arranque aplica migraciones aditivas automáticamente. Se invalidan las sesiones antiguas: todos deben volver a iniciar sesión.
5. Las cuentas que aún usan la antigua contraseña demo quedan desactivadas. Recuperalas con `python admin.py user reset USUARIO` (en Docker: `docker compose exec web python admin.py user reset USUARIO`). El comando pide una contraseña nueva y reactiva la cuenta.
6. Para gestionar cuentas desde la interfaz, concedé el permiso de propietario **una sola vez desde tu consola**: `python admin.py user owner USUARIO`. No se concede automáticamente a los administradores existentes.

No sobrescribas ni borres la base al actualizar. Volver a una versión anterior puede reintroducir las vulnerabilidades; conservar un respaldo no sustituye una migración de reversión.

## Ejecución local

Requiere Python 3.11 o posterior.

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python admin.py user add manuel Rivera
python admin.py user owner manuel
python server.py
```

Abrí http://127.0.0.1:8080. `server.py` utiliza el servidor de desarrollo Flask y escucha en loopback por defecto. Para pruebas en una red local puede definirse `HOST=0.0.0.0` con demo desactivada. GPS/cámara requieren un contexto seguro fuera de localhost.

Las variables deben exportarse en la terminal; Python no carga `.env` automáticamente. Docker Compose sí utiliza `.env` para las variables declaradas en `compose.yaml`.

## Docker / producción

```sh
cp .env.example .env
# Editá .env; DEMO_MODE debe permanecer en 0.
docker compose up --build -d
docker compose exec web python admin.py user add manuel Rivera
docker compose exec web python admin.py user owner manuel
```

### Demo de prueba en Render (v11.1)

Primero subí esta versión al repositorio. En el servicio web de Render → Environment, definí:

| Variable | Valor |
| --- | --- |
| `DEMO_MODE` | `1` |
| `ALLOW_PUBLIC_DEMO` | `1` |
| `DEMO_ADMIN_PASSWORD` | Una contraseña propia de al menos 12 caracteres |
| `DEMO_RIVERA_USER` | `demo_rivera` |
| `DEMO_LIVRAMENTO_USER` | `demo_livramento` |

Conservá `DATABASE_URL`. Guardá y reconstruí/desplegá. Se crean esos usuarios si no existen; ambos usan la contraseña elegida. Las cuentas existentes conservan sus contraseñas: la variable no las restablece. Los nombres nuevos evitan depender de las cuentas heredadas desactivadas. El botón de ejemplos aparece con modo demo activo. No se concede rol propietario a estas cuentas.

Los límites de solicitudes, el TTL, la separación de ciudades y CSP siguen activos. Al terminar, definí `DEMO_MODE=0`, `ALLOW_PUBLIC_DEMO=0` y desactivá las cuentas de prueba desde el propietario; desactivar el modo por sí solo no elimina cuentas ni incidencias.

Docker publica el puerto 8081 y ejecuta Flask mediante Gunicorn, con dos workers y cuatro hilos por worker. El volumen `ciudad_data` conserva SQLite y puede conservar fotos en `/data/photos`. El comando equivalente en Linux es:

```sh
gunicorn --config gunicorn.conf.py 'server:create_app(initialize=False)'
```

El hook de Gunicorn inicializa la base antes de crear workers; cada worker crea su propio pool. No usar `--preload`. Gunicorn no se utiliza en Windows nativo: usá Docker para producción. Las migraciones se ejecutan por despliegue; evitá iniciar dos despliegues que migren la misma base simultáneamente.

`render.yaml` tiene demo desactivada y enlaza PostgreSQL. En un servicio ya existente, revisá sus variables manualmente. `DATABASE_URL` debe ser una URL PostgreSQL válida. Para exposición pública, configurá HTTPS en el proxy/hosting. El archivo `compose.https.yaml` conserva la opción de Caddy para pruebas locales.

### IP real detrás del proxy

Por defecto se usa la IP de conexión y se ignora `X-Forwarded-For`. Si el proxy reemplaza/sanea ese encabezado, definí `TRUST_PROXY_HOPS` con el número exacto de proxies confiables. No habilites ese ajuste con un servidor accesible directamente desde Internet. Si no se configura detrás de un proxy, los visitantes pueden compartir el mismo límite por IP; es conservador pero puede bloquear usuarios legítimos.

## Seguridad

- Login: máximo 5 intentos por usuario normalizado cada 15 minutos; un éxito borra su contador. Máximo 30 intentos por IP en 15 minutos, incluidos los exitosos. Se responde `429` con `Retry-After`. Los contadores son atómicos y compartidos por los workers mediante la base de datos.
- Incidencias: 10 solicitudes por IP cada 10 minutos, configurables. Otros envíos ciudadanos comparten un límite de 60 por IP cada 10 minutos. Se cuentan también solicitudes fallidas e intentos de duplicados; `force_new` no evita el límite. No se incluye CAPTCHA de un proveedor externo.
- Sesiones: 12 horas por defecto, configurables entre 1 y 24; no se renuevan por actividad. Se almacenan hashes SHA-256 de tokens aleatorios. Se valida el TTL y la cuenta activa en cada solicitud; limpieza cada 5 minutos.
- Desactivar una cuenta o restablecer su contraseña revoca sus sesiones. La migración invalida tokens antiguos sin hash/TTL.
- CSP en respuestas estáticas y API. Scripts solo del mismo origen; `object-src`, `base-uri` y frames restringidos. Se permiten estilos inline porque Leaflet y los gráficos los necesitan; no se permiten scripts inline.
- Fotos redecodificadas como JPEG, con orientación corregida y metadatos descartados. Hasta 3 fotos, 3 MiB por foto y 12 MiB por solicitud JSON. Protección contra imágenes corruptas y bombas de descompresión.
- Una foto no publicada requiere un token válido de su ciudad, incluso cuando está guardada en disco.
- Un `device` no equivale a una identidad verificada: las confirmaciones representan aportes, no personas únicas. El límite por IP reduce abuso, pero no detiene una botnet distribuida.

## Panel, cuentas e informes

El panel muestra 50 incidencias por página. Búsqueda, ciudad, categoría y estado se filtran en SQL. El mapa y sus indicadores describen la página visible; los informes usan agregaciones independientes sobre **todas** las incidencias de la ciudad en el período indicado (fechas UTC), sin recortarlas a la página o a los filtros de la lista.

Seleccioná varias filas para aprobarlas/asignarlas en un lote de hasta 50. Una ciudad incorrecta, revisión desactualizada o selección inválida rechaza el lote completo. Se conserva historial y se intentan enviar notificaciones a suscriptores. La selección pausa el refresco automático del panel para no perderla.

El propietario tiene un botón **Cuentas** para crear administradores, restablecer contraseñas y desactivar cuentas. Los administradores normales no tienen acceso a esa API. El rol propietario permite gestionar cuentas de ambas ciudades, pero sus reportes continúan limitados a la ciudad de su cuenta. Los propietarios se crean/promueven/restablecen desde consola; no existe registro público. La recuperación es asistida por propietario, sin flujo de correo de autoservicio.

```sh
python admin.py user list
python admin.py user reset USUARIO
python admin.py user owner USUARIO
python admin.py responsible add "Santana do Livramento" "Equipe de limpeza"
```

## Configuración

| Variable | Predeterminado / función |
| --- | --- |
| `DATABASE_URL` | Vacío: SQLite; URL PostgreSQL: pool por worker |
| `DATA_DIR` | `./data`; Docker: `/data` |
| `SESSION_HOURS` | `12`, rango 1–24 |
| `INCIDENT_RATE_LIMIT` | `10` solicitudes / IP / 10 minutos |
| `DB_POOL_SIZE` | `8` conexiones máximas por worker; mínimo 1 |
| `WEB_CONCURRENCY`, `WEB_THREADS` | `2`, `4` |
| `TRUST_PROXY_HOPS` | `0`; configurar solo con un proxy confiable |
| `DEMO_MODE` | `0`; `1` para demo; en hosting requiere `ALLOW_PUBLIC_DEMO=1` |
| `DEMO_ADMIN_PASSWORD` | Sin valor compartido; mínimo 12 caracteres |
| `PHOTOS_DIR` | Vacío: BLOB; ruta persistente: archivos JPEG privados |
| `TILE_URL` | Tiles de OpenStreetMap; su origen se agrega a CSP |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | IA externa opcional, modelo existente `gpt-5-nano` |
| `AI_TIMEOUT_SECONDS` | `8` segundos de timeout de conexión/lectura de IA |
| `AI_INPUT_USD_PER_MILLION`, `AI_OUTPUT_USD_PER_MILLION` | `0`; ingresar tarifas para una estimación simple |
| `LOCAL_EMBED_MODEL` | Carpeta de modelo Sentence Transformers previamente descargado |
| `SENTRY_DSN` | Vacío; integración de errores opcional |
| `LOG_LEVEL` | `INFO` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_TLS` | Notificaciones opcionales; puerto 587 y TLS por defecto |

Ajustá el pool según el límite de conexiones del proveedor: `workers × DB_POOL_SIZE`, más conexiones de administración. El timeout SQL de PostgreSQL es 10 segundos y la espera del pool, 5 segundos.

## Fotos fuera de la base

Definí `PHOTOS_DIR` en un volumen persistente, por ejemplo `/data/photos` en Docker. Las fotos nuevas se guardan con nombres aleatorios y la base conserva referencias; las antiguas siguen leyéndose como BLOB. No se copian automáticamente las fotos antiguas a disco.

Respaldá **base y directorio de fotos juntos**. Todos los workers/instancias deben acceder al mismo directorio. Si el despliegue no ofrece disco persistente compartido, mantené BLOB hasta configurar un adaptador de objetos. No se incluye integración S3 ni se provisionan servicios externos. Una transacción fallida puede dejar un archivo huérfano, que no es accesible desde la API; su limpieza no está automatizada.

## IA y observabilidad

Tras tres fallos consecutivos de OpenAI, el circuito se abre durante 180 segundos y se responde con análisis local. Al terminar ese intervalo, una solicitud prueba la recuperación. El circuito y los contadores son por worker. El timeout de `urllib` controla operaciones de red, no constituye un límite total estricto de duración ante un servidor que entregue datos muy lentamente.

`GET /api/admin/metrics` requiere sesión y muestra llamadas, éxitos, errores, fallbacks, latencia acumulada, tokens y costo estimado del worker que respondió. Los logs emiten métricas de cada intento externo; para métricas globales persistentes hay que recolectar esos logs. Los costos son estimaciones según tarifas configuradas y no contemplan descuentos por caché. Cero con tarifas no configuradas **no significa costo gratuito**.

`GET /api/health` ejecuta `SELECT 1` y devuelve 503 si falla la base. No expone credenciales ni la excepción al cliente. Los logs utilizan niveles y Gunicorn omite parámetros de consulta en su access log. `SENTRY_DSN` permite conectar tu propio proyecto; se deshabilita el envío de PII predeterminada y se eliminan contexto de request/usuario/breadcrumbs del evento.

### Embeddings locales opcionales

```sh
pip install -r requirements-local-ai.txt
# Definí LOCAL_EMBED_MODEL con la carpeta de un modelo multilingüe ES/PT.
```

La aplicación solo carga archivos locales y no descarga modelos ni permite `trust_remote_code`. Si el modelo/dependencias faltan, mantiene la comparación léxica. Los embeddings se combinan con distancia y categoría; el umbral requiere evaluación con reportes reales. No se incluye el modelo en el ZIP ni se validó su precisión. El consumo de RAM y CPU depende del modelo y se multiplica por worker.

## Pruebas y alcance de verificación

```sh
python -m unittest discover -v
node --check public/app.js
```

Incluye flujos existentes, autorización por ciudad/propietario, bloqueo y recuperación del login, TTL/hash/logout, migración, límites concurrentes, JSON inválido, fotos corruptas/grandes, almacenamiento en disco, lotes atómicos, conflictos de revisión, paginación, informes completos y circuito IA simulado. GitHub Actions ejecuta la suite y sintaxis JS en cada push/PR con Python 3.11–3.13.

En esta actualización se ejecutó la suite sobre SQLite y una comprobación de arranque Gunicorn con dos workers. PostgreSQL, OpenAI real, Sentry, SMTP y un modelo de embeddings requieren verificación en tu entorno; las pruebas locales no llaman servicios pagos. No se pudo completar la verificación visual en navegador por indisponibilidad de Chromium en el entorno de trabajo.

## Historial, licencia y dependencias

El historial está en [CHANGELOG.md](CHANGELOG.md). La licencia del código propio queda pendiente de elección por su titular; no se asigna una licencia abierta sin esa decisión. Leaflet conserva su archivo `public/vendor/leaflet/LICENSE`; las demás dependencias conservan sus propias licencias.

Referencias de implementación: [Flask/Gunicorn](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/), [pool Psycopg](https://www.psycopg.org/psycopg3/docs/advanced/pool.html), [Sentence Transformers](https://sbert.net/docs/package_reference/sentence_transformer/model.html).

## Ubicación en el detalle (v11.2)

Los detalles públicos y administrativos muestran ciudad, coordenadas y calle/referencia cuando fue informada. El botón «Ver ubicación en el mapa» despliega el punto exacto dentro del detalle sin perder lo escrito en el formulario de aprobación. Incluye un enlace opcional a OpenStreetMap en otra pestaña. Los reportes nuevos admiten calle/número/referencia opcional; los antiguos con dirección genérica muestran «Dirección no informada». No se infiere ni geocodifica automáticamente la dirección. Se conserva la configuración de demo pública de v11.1.

## Actividades comunitarias (v11.3)

Las oportunidades muestran tareas concretas, problema vinculado, dirección/ciudad, horario e interesados. «Ver actividad y ubicación» abre el detalle completo con el mapa del reporte, su descripción y foto cuando existe, punto de encuentro, organización e indicaciones. La ubicación del mapa corresponde al reporte; el punto de encuentro es una referencia textual definida por la administración.

Desde el detalle administrativo, «Crear acción comunitaria» abre un formulario: las tareas son obligatorias y fecha/horario local, encuentro, responsable e indicaciones son opcionales. Una actividad existente tiene «Editar actividad y detalles». Las ediciones conservan interesados y verifican revisión para evitar sobrescribir cambios ajenos. Las actividades antiguas mantienen sus datos y muestran «Por confirmar» en campos aún no completados: no se inventan tareas, fechas ni responsables.

Si el reporte deja de ser público, su actividad tampoco se lista públicamente ni recibe nuevos intereses. Todo cambio administrativo sigue limitado a la ciudad de la cuenta.

## Gestión individual y modal estable (v11.4)

- El detalle de una incidencia bloquea el scroll de la página de fondo y usa un desplazamiento interno propio, tanto en escritorio como en móvil.
- Se eliminaron los checkboxes y la edición masiva del listado administrativo porque la gestión se realiza ticket por ticket.
- El refresco automático del panel continúa funcionando cada 10 segundos sin depender de selecciones múltiples.
