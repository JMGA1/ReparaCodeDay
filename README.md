# Repara v10 · Online-ready

Esta versión mantiene **SQLite + Docker local** y agrega **PostgreSQL para Render**. Las sesiones administrativas se guardan en la base de datos y el navegador conserva el token durante la pestaña/sesión, por lo que cambiar entre Mapa ciudadano y Panel privado ya no cierra la sesión. El panel consulta datos nuevamente cada 10 segundos para recibir reportes creados desde otros dispositivos. Las fotos se guardan en la misma base de datos.







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



```powershell
docker compose exec web python admin.py user add livramento_admin "Santana do Livramento"
```

El comando solicita la contraseña dos veces y guarda solamente un hash PBKDF2, no la contraseña en texto plano. Para listar las cuentas:

```powershell
docker compose exec web python admin.py user list
```


Para ver la lista:

```powershell
docker compose exec web python admin.py responsible list
```



## Referencias

- Leaflet: https://leafletjs.com/examples/quick-start/ (se incluye su licencia BSD).
- Mapas: https://operations.osmfoundation.org/policies/tiles/ (sin descarga masiva ni mapas offline).
- Captura de foto: https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/capture
- Geolocalización: https://developer.mozilla.org/en-US/docs/Web/API/Geolocation_API
- PWA: https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable
- HTTPS local: https://caddyserver.com/docs/automatic-https

---



Los reportes ciudadanos nuevos se crean con estado **Recibido** y permanecen privados. No aparecen en el mapa ni en la lista pública, y sus fotos tampoco pueden consultarse sin una sesión administrativa de la ciudad correspondiente.

Desde el **Panel privado**, el administrador revisa el reporte y puede:

- **Aprobar y publicar**: cambia el estado a **En revisión**; desde ese momento aparece en el mapa público.
- **Rechazar**: cambia a **Rechazado** y permanece fuera de la vista pública.
- **Marcar duplicada**: cambia a **Duplicado** y permanece fuera de la vista pública.

Los estados públicos son **En revisión**, **En proceso** y **Resuelto**. El ciudadano recibe al crear el reporte un código de seguimiento y un aviso indicando que la incidencia será revisada antes de publicarse.


