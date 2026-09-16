# Historial

## v11.4 — Gestión individual y scroll del modal

- El modal administrativo bloquea el scroll de la página de fondo y desplaza únicamente su contenido.
- Se eliminaron los checkboxes de selección y la acción masiva del listado de incidencias.
- Se simplificó el listado para reflejar el flujo real: una incidencia se abre y se gestiona individualmente.

## v11.3 — Jornadas comunitarias detalladas

- Tarjetas con actividad, problema, dirección, ciudad y horario.
- Detalle con mapa, reporte original, foto, punto de encuentro, responsable e indicaciones.
- Formulario administrativo para crear/editar tareas y organización; control de revisión.
- Conservación de interesados y campos antiguos sin inventar información.
- Actividades ocultas cuando el reporte deja de ser público.
- Pruebas de edición, ubicación, autorización y privacidad.

## v11.2 — Ubicación visible al revisar

- Dirección/referencia, ciudad y coordenadas en el detalle.
- Mapa desplegable centrado en el reporte, sin cerrar la aprobación.
- Campo opcional de calle/referencia para reportes nuevos.
- Textos ES/PT y enlace a OpenStreetMap.

## v11.1 — Demo pública explícita

- `ALLOW_PUBLIC_DEMO=1` permite una presentación alojada sin retirar la exigencia de contraseña propia ni las protecciones de sesión y solicitudes.
- Guía de Render con cuentas de prueba separadas de las cuentas heredadas.

## v11 — Seguridad, escalabilidad y mantenimiento

- Flask + Gunicorn en Docker; cierre de conexiones SQLite y pool PostgreSQL por worker.
- Límites persistentes y atómicos de login/envíos; `429` y `Retry-After`.
- Sesiones de duración limitada, tokens con hash, limpieza periódica y revocación por cuenta.
- Demo desactivada por defecto y bloqueada en producción; cuentas con la contraseña heredada desactivadas.
- CSP compatible con Leaflet, validación reforzada de fotos y cuerpos JSON limitados.
- Paginación y filtros SQL; informes agregados independientes de la página.
- Edición masiva atómica por ciudad y revisión.
- Hasta tres fotos por incidencia; almacenamiento privado opcional en disco con lectura compatible de BLOB anteriores.
- Gestión de cuentas reservada al propietario; recuperación asistida y desde consola.
- Circuit breaker y timeout de IA reducido; métricas y costos configurables; embeddings locales opcionales.
- Logging, health check de base de datos e integración Sentry opcional.
- Suite ampliada, workflow CI, formato Python legible y documentación reorganizada.

## v10.8

- Recomendación de sector por IA y sugerencias de acciones comunitarias seguras.
- Validación administrativa de iniciativas y registro de interés ciudadano.
- Mejoras del mapa y simplificación del panel presentes en el ZIP base.

## v10.3

- Corrección de login.

## v10

- Preparación para despliegue online y soporte PostgreSQL/Render.

## v8

- Moderación obligatoria antes de publicar incidencias y fotos.

## v7

- Modo presentación y cuentas demo (retiradas como opción pública en v11).

## v6

- Ajustes y simplificación de interfaz.

## v5

- Clasificación con IA externa opcional y traducción ES/PT.

## v4

- Reporte simplificado, selección de ubicación y análisis de duplicados.

## v3

- Cuentas administrativas por ciudad, responsables, reportes y despliegue local.

El archivo base no incluye un historial separado verificable de v1/v2. Las guías antiguas fueron reemplazadas por las instrucciones actuales del README.
