# Historial

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
