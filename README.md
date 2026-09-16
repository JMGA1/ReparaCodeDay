# Repara v10 · Online-ready

Esta versión mantiene **SQLite + Docker local** y agrega **PostgreSQL para Render**. Las sesiones administrativas se guardan en la base de datos y el navegador conserva el token durante la pestaña/sesión, por lo que cambiar entre Mapa ciudadano y Panel privado ya no cierra la sesión. El panel consulta datos nuevamente cada 10 segundos para recibir reportes creados desde otros dispositivos. Las fotos se guardan en la misma base de datos.

## Render: conectar PostgreSQL al servicio ya publicado

1. En Render: **New → Postgres**. Creá una base (por ejemplo `repara-db`) en la **misma región** que tu Web Service.
2. Abrí la base creada → **Connect** y copiá la **Internal Database URL**.
3. Abrí tu Web Service Repara → **Environment** → agregá `DATABASE_URL` con esa URL interna.
4. Conservá `DEMO_MODE=1` para las cuentas de evaluación. Podés definir `DEMO_ADMIN_PASSWORD` con tu propia contraseña.
5. Hacé **Save and deploy** / **Deploy latest commit**. En los logs debe aparecer `Base de datos: PostgreSQL.`
6. Probá desde el celular: creá una incidencia. Queda en `Recibido` (privada). En hasta ~10 s debe aparecer en el Panel privado de la ciudad correspondiente. Al aprobarla y pasarla a `En revisión`, aparece en el mapa público.

> `DATABASE_URL` nunca debe subirse a GitHub. En Render se configura como variable de entorno.

## Docker local

Sin `DATABASE_URL`, Repara usa SQLite automáticamente:

```bash
docker compose up --build -d
```

Abrí `http://localhost:8081`.

---

## Cambios de UI v6

- Nueva portada/hero para dejar claro que Ciudad Visible sirve para reportar incidencias urbanas desde web o celular.
- La prioridad sugerida por IA queda visible solo en el panel privado; no se muestra al ciudadano.
- La foto del reporte se destaca en Gestión con vista ampliada y enlace para abrirla completa.

# Ciudad Visible v3 — Rivera y Santana do Livramento

Webapp para reportar incidencias desde computadora o celular. Prototipo académico independiente, sin conexión con organismos públicos.

## Qué cambió

- Mapa geográfico real de Rivera y Santana do Livramento, con Leaflet y OpenStreetMap.
- Desplazamiento con mouse/dedo y zoom con rueda, botones o gesto de dos dedos.
- Selección del punto al tocar el mapa y marcador arrastrable.
- Ubicación GPS cuando el navegador y la conexión segura lo permiten.
- Dos opciones de foto: **Tomar foto** (cámara trasera preferida) y **Elegir de galería**.
- Fotos reducidas a un máximo de 1600 píxeles y convertidas a JPEG sin conservar metadatos EXIF.
- Reportes, fotos, responsables e historial guardados en **SQLite**, en un volumen Docker.
- Consultas compartidas entre dispositivos conectados al mismo servidor.
- Panel privado con cuentas administrativas individuales creadas únicamente desde el servidor.
- Cada cuenta administrativa queda vinculada a **Rivera** o **Santana do Livramento** y solo puede ver/modificar datos de esa ciudad.
- Gestión e informes reunidos en un único panel privado.
- Responsables seleccionables mediante lista, sin texto libre.
- Selector español/portugués y modo oscuro.
- Manifest y service worker para instalación PWA cuando hay HTTPS confiable o localhost.
- Informes administrativos filtrados automáticamente por la ciudad de la cuenta.

## 1. Reemplazar la demo anterior

La demo anterior usaba el puerto 8081. Primero detenela: abrí PowerShell en la carpeta anterior (`ciudad-visible-docker`) y ejecutá:

```powershell
docker compose down
```

También podés detener el contenedor anterior desde Docker Desktop.

Extraé este ZIP **en una carpeta nueva**. Entrá en `ciudad-visible-v2`, donde están `Dockerfile`, `compose.yaml` y `server.py`, y abrí PowerShell allí.

```powershell
docker compose up --build -d
```

La primera construcción requiere Internet para descargar Python y Pillow. En la PC, abrí:

**http://localhost:8081**

No hace falta instalar Python en Windows; queda dentro de Docker. Esta versión necesita el servidor: abrir `index.html` directamente ya no es suficiente.

La base comienza vacía para no mostrar reclamos ficticios como si fueran reales. Desde cada Panel privado se pueden cargar ejemplos de esa ciudad.

## 2. Usarla desde el celular

1. Conectá PC y celular a **la misma red Wi-Fi**. Mantené la computadora encendida y Docker funcionando.
2. En PowerShell ejecutá:

```powershell
ipconfig
```

3. Buscá la **Dirección IPv4** del adaptador Wi-Fi o Ethernet que conecta la computadora a tu router. No uses una dirección de los adaptadores virtuales de Docker/WSL.
4. Si esa dirección fuera `192.168.1.25`, abrí en el navegador del celular:

**http://192.168.1.25:8081**

Sustituí esa IP de ejemplo por la tuya. `localhost` en el celular apunta al propio celular, no a tu computadora.

5. Tocá **Reportar problema**, elegí categoría y ciudad y marcá el lugar en el mapa.
6. Elegí **Tomar foto** o **Elegir de galería**, revisá la vista previa y enviá.
7. En la PC, tocá **Actualizar**: aparecerá el mismo reporte con su foto.

La captura mediante `input type=file` depende del navegador y del sistema. El botón solicita la cámara trasera, pero algunos navegadores muestran un selector. Si no abre la cámara, tomá la foto con la app de cámara y elegila desde la galería. No se requiere cámara para registrar un reporte.

### Si el teléfono no puede abrir la página

- Verificá que la página sí abra en la PC y que ambos estén en la misma red.
- Permití Docker Desktop en el Firewall de Windows para **redes privadas** cuando Windows lo solicite.
- La red de invitados de un router o la red de una facultad puede bloquear conexiones entre dispositivos. Probá en una red donde el router permita comunicación local.
- Si la IP de la PC cambia, usá la nueva dirección.
- No hace falta abrir puertos del router a Internet.

### HTTP, cámara, GPS e instalación

| Función | HTTP en la Wi-Fi | HTTPS confiable |
| --- | --- | --- |
| Mapa y selección manual del punto | Sí, con Internet para el mapa | Sí |
| Foto mediante selector de archivo/cámara | Según el navegador; galería como alternativa | Según el navegador |
| GPS del teléfono | Normalmente bloqueado | Disponible con permiso |
| Instalación PWA y service worker | No en una IP local por HTTP | Disponible según el navegador |

No se usa `getUserMedia` ni una cámara de video embebida: se abre el flujo de captura de imágenes del dispositivo. El servidor recibe la foto al enviar el formulario, no antes.

## 3. Crear cuentas y entrar al Panel privado

No existe registro público de administradores. Las cuentas se crean únicamente desde la consola por quien administra el servidor.

Para crear la cuenta de Rivera:

```powershell
docker compose exec web python admin.py user add rivera_admin Rivera
```

Para Santana do Livramento (la ciudad lleva espacios, por eso va entre comillas):

```powershell
docker compose exec web python admin.py user add livramento_admin "Santana do Livramento"
```

El comando solicita la contraseña dos veces y guarda solamente un hash PBKDF2, no la contraseña en texto plano. Para listar las cuentas:

```powershell
docker compose exec web python admin.py user list
```

Después abrí **Panel privado** e ingresá con el usuario correspondiente. La sesión vive en memoria del servidor y no habilita acceso a la otra ciudad.

Cada ciudad ya incluye responsables/equipos genéricos seleccionables. Si querés agregar otro responsable:

```powershell
docker compose exec web python admin.py responsible add Rivera "Nombre o equipo"
docker compose exec web python admin.py responsible add "Santana do Livramento" "Nome ou equipe"
```

Para ver la lista:

```powershell
docker compose exec web python admin.py responsible list
```

Desde el Panel privado podés cambiar estado, seleccionar responsable, agregar notas, cargar ejemplos y generar informes. Todos esos datos quedan limitados a la ciudad asociada a la cuenta.

## 4. HTTPS local para GPS e instalación

Incluimos una configuración opcional con **Caddy**. Crea un certificado para la IP de tu PC. Para que sea confiable en el celular, hay que instalar allí el certificado raíz de esta instalación. Caddy dentro de Docker no puede hacerlo automáticamente en el teléfono.

1. En la carpeta nueva, copiá el archivo de ejemplo:

```powershell
Copy-Item .env.example .env
notepad .env
```

2. Cambiá `LAN_IP=192.168.1.25` por la IPv4 real de tu PC y guardá.
3. Iniciá también el servidor HTTPS:

```powershell
docker compose -f compose.yaml -f compose.https.yaml up --build -d
```

4. Copiá el certificado público generado:

```powershell
docker compose -f compose.yaml -f compose.https.yaml cp https:/data/caddy/pki/authorities/local/root.crt ./ciudad-visible-root.crt
```

Si aún no existe, revisá los mensajes del servicio y esperá a que termine de iniciar:

```powershell
docker compose -f compose.yaml -f compose.https.yaml logs https
```

5. Transferí `ciudad-visible-root.crt` a tu propio teléfono e instalalo como certificado de CA de confianza. Los menús varían:

- **Android:** en Ajustes, buscá «Instalar certificado» o «Certificado de CA». Seleccioná el archivo generado por tu instalación.
- **iPhone/iPad:** abrí el certificado, instalá el perfil desde Ajustes y activá su confianza en General → Información → Ajustes de confianza de los certificados.

El certificado raíz habilita la confianza en los certificados que emite esta instalación. Conservá privada la clave del servidor Caddy; no compartas su volumen ni archivos `.key`. Podés retirar el certificado del teléfono al terminar las pruebas. No hace falta desactivar controles de seguridad del navegador.

6. Abrí en el teléfono, usando tu IP:

**https://192.168.1.25:8443**

La página debe abrir **sin advertencia de certificado**. Simplemente ignorar una advertencia no reemplaza una conexión confiable para las funciones PWA/GPS.

7. Probá «Usar mi ubicación». Aceptá el permiso si querés localizar el punto. Revisá su precisión y ajustalo manualmente.
8. Para instalar: en Android, usá «Instalar app» cuando aparezca o el menú del navegador; en iPhone, Compartir → Agregar a pantalla de inicio.

Si cambia la IP de la computadora, actualizá `.env` y ejecutá nuevamente el comando con ambos archivos Compose. Si cambiás de HTTP a HTTPS, tu identificador local del navegador cambia porque es otro origen; los reportes del servidor se conservan.

Esta configuración es para pruebas locales. Una versión pública debería usar un dominio con HTTPS válido, un servidor de aplicación de producción, cuentas y roles, moderación y controles de abuso antes de recibir reportes abiertos.

## 5. Datos, respaldo y reinicio

Los datos viven en el volumen `ciudad-visible-v2_ciudad_data`. Se conservan al ejecutar `docker compose down`, reiniciar Docker o reconstruir la imagen. No ejecutes `docker compose down -v` si querés conservarlos: esa opción borra volúmenes.

Para respaldar la base SQLite de manera consistente mientras el servidor está activo:

```powershell
docker compose exec web python -c "import sqlite3; src=sqlite3.connect('/data/ciudad.sqlite3'); dst=sqlite3.connect('/data/respaldo.sqlite3'); src.backup(dst); dst.close(); src.close()"
docker compose cp web:/data/respaldo.sqlite3 ./respaldo.sqlite3
```

La copia de la base incluye las fotos, el historial, las cuentas administrativas y sus hashes de contraseña. No copies solo `ciudad.sqlite3` mientras el servidor esté activo: SQLite usa archivos WAL adicionales.

Para detener la versión HTTP:

```powershell
docker compose down
```

Si habilitaste HTTPS, detené ambos servicios:

```powershell
docker compose -f compose.yaml -f compose.https.yaml down
```

## 6. Qué es real y qué sigue siendo prototipo

**Implementado:** mapa real, coordenadas, captura/adjunto de imagen, persistencia SQLite, lectura desde varios dispositivos, cuentas administrativas por ciudad, panel unificado de gestión e informes, responsables seleccionables, historial, indicadores, español/portugués, modo oscuro y archivos PWA.

**Pendiente:** integración con organismos, recuperación de contraseña, moderación avanzada, notificaciones y límites oficiales de barrios/jurisdicciones.

La ciudad la indica el usuario. El servidor valida una caja geográfica amplia alrededor de ambas ciudades; no afirma validar los límites municipales. Los posibles duplicados se buscan por categoría y distancia menor a 100 metros. Los ejemplos ficticios se excluyen de esa búsqueda.

Los mapas requieren Internet. El service worker guarda únicamente archivos de interfaz; no almacena mapas, fotos o respuestas de la API, ni encola reportes. Si el servidor está desconectado, el formulario informa el fallo y permite reintentar sin perder el contenido mientras la pestaña siga abierta. No promete envío offline.

La primera versión no tenía persistencia y sus cambios vivían en una pestaña; no hay una base anterior que migrar.

## 7. Estructura y pruebas

- `public/`: interfaz, Leaflet local, formulario, manifest y service worker.
- `server.py`: API HTTP local y acceso a SQLite.
- `requirements.txt`: Pillow para validar y recodificar imágenes.
- `compose.yaml` y `Dockerfile`: aplicación y volumen persistente.
- `compose.https.yaml` y `Caddyfile`: HTTPS local opcional.
- `test_api.py`: pruebas de creación, persistencia tras reinicio, imagen, autenticación, validación, conflictos y confirmaciones sin duplicación.

Podés ejecutar las pruebas dentro del contenedor con:

```powershell
docker compose cp test_api.py web:/tmp/test_api.py
docker compose exec -w /app web python -m unittest discover -s /tmp -p test_api.py -v
```

Alternativamente, fuera de Docker, instalá Python 3.12 o superior y las dependencias y ejecutá `python -m unittest -v test_api.py` desde la carpeta del proyecto.

**Verificación realizada:** pruebas de API aprobadas, reinicio con conservación de datos y fotos comprobado; sintaxis Python/JavaScript y archivos de configuración revisados. El entorno de preparación no tiene Docker y el navegador de pruebas no pudo conectar al servidor local. No se verificaron aquí la construcción Docker/Caddy, los gestos táctiles ni la cámara/GPS de un teléfono físico. Validalos siguiendo el recorrido de las secciones 1–4 antes de la presentación.

## Referencias

- Leaflet: https://leafletjs.com/examples/quick-start/ (se incluye su licencia BSD).
- Mapas: https://operations.osmfoundation.org/policies/tiles/ (sin descarga masiva ni mapas offline).
- Captura de foto: https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/capture
- Geolocalización: https://developer.mozilla.org/en-US/docs/Web/API/Geolocation_API
- PWA: https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable
- HTTPS local: https://caddyserver.com/docs/automatic-https

---

## Novedades v4: reporte simplificado

El formulario ciudadano ahora pide solamente:

1. **Descripción del problema**.
2. **Ubicación**: GPS o un toque en el mapa.
3. **Foto opcional**.

La aplicación crea automáticamente un título corto, usa reglas simples para sugerir una categoría y asigna la jurisdicción según la ubicación. Esta clasificación **todavía no usa IA**; queda preparada para reemplazarse por un clasificador inteligente en una versión posterior.

Cada incidencia obtiene un código público único, por ejemplo:

- `RIV-2026-00001` para Rivera.
- `SLV-2026-00002` para Santana do Livramento.

Después de registrar el reporte se muestra una confirmación grande, el código de seguimiento y la opción de dejar un email. También se mantiene el botón **“También vi este problema”** y, durante el alta, se sugieren incidencias activas que estén muy cerca para evitar duplicados.

## GPS desde el celular: HTTPS local

Los navegadores móviles normalmente bloquean la geolocalización cuando la página se abre como `http://192.168.x.x:8081`. Para probar GPS en la misma Wi-Fi se incluye Caddy con HTTPS local.

### 1. Obtener la IPv4 de la PC

En PowerShell:

```powershell
ipconfig
```

Buscá la IPv4 de la tarjeta Wi-Fi, por ejemplo `192.168.1.25`.

### 2. Crear `.env`

Copiá `.env.example` como `.env` y cambiá:

```text
LAN_IP=192.168.1.25
```

### 3. Levantar web + HTTPS

```powershell
docker compose -f compose.yaml -f compose.https.yaml up --build -d
```

La app HTTP sigue disponible en:

```text
http://localhost:8081
```

Y desde el celular, después de confiar en el certificado local:

```text
https://192.168.1.25:8443
```

### 4. Confiar en el certificado de Caddy en el celular

Caddy genera una autoridad certificadora local. Después del primer arranque, el certificado raíz queda en:

```text
caddy-data\caddy\pki\authorities\local\root.crt
```

Copiá `root.crt` al teléfono e instalalo como certificado de autoridad de confianza. Los nombres exactos de los menús cambian según Android/iOS. En iPhone, después de instalar el perfil también hay que habilitar confianza total para el certificado raíz en los ajustes de certificados.

Luego cerrá y reabrí el navegador y entrá a `https://TU_IP:8443`. Si el navegador muestra el candado/HTTPS válido, **Usar mi ubicación actual** podrá solicitar permiso de GPS.

> El certificado de Caddy es únicamente para pruebas en tu red local. Para una publicación real conviene un dominio con un certificado HTTPS público.

## Email opcional

La suscripción por email funciona en dos niveles:

- Sin SMTP configurado: el email queda guardado en SQLite asociado a la incidencia.
- Con SMTP configurado: al cambiar estado/responsable desde el panel privado, el servidor envía una actualización a los emails suscriptos.

Podés configurar estas variables en `.env`:

```text
SMTP_HOST=smtp.ejemplo.com
SMTP_PORT=587
SMTP_USER=usuario
SMTP_PASSWORD=clave-o-app-password
SMTP_FROM=Ciudad Visible <no-reply@ejemplo.com>
SMTP_TLS=1
```

No es necesario configurar SMTP para probar el resto de la v4.

---

## Versión 5 · IA + traducción completa ES/PT

Esta versión agrega una capa de análisis automático al crear una incidencia:

- categoría automática;
- título breve generado a partir de la descripción;
- resumen neutral;
- prioridad sugerida (`Baja`, `Media`, `Alta`, `Urgente`);
- explicación de por qué se sugiere esa prioridad;
- comparación con incidencias activas dentro de 250 m para detectar posibles duplicados;
- si hay una coincidencia fuerte, el ciudadano puede elegir **También vi este problema** o **Crear una incidencia nueva de todos modos**;
- el administrador puede modificar la prioridad manualmente en el panel privado.

La interfaz ahora genera los textos directamente en español o portugués, en vez de traducir el DOM después de renderizar. Esto cubre también modales, estados, categorías, prioridades, mensajes de GPS, confirmaciones, email y panel administrativo.

### Probar sin IA externa

No hace falta configurar nada. Ejecutá:

```powershell
docker compose up --build -d
```

La aplicación usa un fallback local de reglas para categoría, prioridad y duplicados. En el detalle aparecerá `Clasificación local (IA no configurada)` / `Classificação local (IA não configurada)`.

### Activar IA real con OpenAI

Copiá `.env.example` como `.env` y agregá tu clave:

```env
OPENAI_API_KEY=tu_clave_aqui
OPENAI_MODEL=gpt-5-nano
```

Luego reconstruí:

```powershell
docker compose down
docker compose up --build -d
```

La clave se usa solamente en el backend y **no se envía al navegador**. El modelo puede cambiarse con `OPENAI_MODEL`.

Para esta tarea se usa la API `Responses` con salida JSON estructurada. Si la API no responde, la clave es inválida o ocurre un error, el servidor vuelve automáticamente al fallback local y el reporte igualmente puede registrarse.

### Cómo comprobar que la IA está activa

Abrí:

```text
http://localhost:8081/api/config
```

Con clave configurada debería aparecer:

```json
{"ai_enabled": true, "ai_model": "gpt-5-nano"}
```

Sin clave:

```json
{"ai_enabled": false, "ai_model": null}
```

### Prueba recomendada de duplicados

1. Creá una incidencia: `Hay un semáforo apagado y peligroso en esta esquina`.
2. En prácticamente el mismo punto intentá crear: `El semáforo de esta esquina sigue apagado y no funciona`.
3. El sistema debería advertir que podría tratarse del mismo problema.
4. Elegí **También vi este problema** para sumar un aporte, o **Crear una incidencia nueva de todos modos** para ignorar la sugerencia.

### Pruebas automatizadas

```powershell
docker compose exec web python -m unittest -v test_api.py
```

También pueden ejecutarse localmente con Python si están instaladas las dependencias.

## Modo presentación (v7 demo-ready)

Esta versión arranca lista para una exposición con `docker compose up --build -d`.
No necesita una API key para que funcionen la categoría automática, título/resumen, detección de posibles duplicados y sugerencia interna de prioridad: cuando no hay `OPENAI_API_KEY`, se usa el motor automático local integrado.

Cuentas creadas automáticamente en el primer arranque:

- Rivera: usuario `rivera` — contraseña `CiudadVisible2026!`
- Santana do Livramento: usuario `livramento` — contraseña `CiudadVisible2026!`

Estas credenciales son solo para presentación/local. Antes de publicar el sistema, desactive `DEMO_MODE` y cree cuentas propias con `admin.py`.

Si más adelante agrega `OPENAI_API_KEY` en `.env`, la misma aplicación pasa a usar el modelo configurado en `OPENAI_MODEL` sin cambios de código. Si la API falla, vuelve automáticamente al motor local.

## Moderación antes de publicar (v8)

Los reportes ciudadanos nuevos se crean con estado **Recibido** y permanecen privados. No aparecen en el mapa ni en la lista pública, y sus fotos tampoco pueden consultarse sin una sesión administrativa de la ciudad correspondiente.

Desde el **Panel privado**, el administrador revisa el reporte y puede:

- **Aprobar y publicar**: cambia el estado a **En revisión**; desde ese momento aparece en el mapa público.
- **Rechazar**: cambia a **Rechazado** y permanece fuera de la vista pública.
- **Marcar duplicada**: cambia a **Duplicado** y permanece fuera de la vista pública.

Los estados públicos son **En revisión**, **En proceso** y **Resuelto**. El ciudadano recibe al crear el reporte un código de seguimiento y un aviso indicando que la incidencia será revisada antes de publicarse.
