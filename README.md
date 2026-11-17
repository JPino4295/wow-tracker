# WoW Farm Tracker

Web ligera para llevar el seguimiento de farmeo de recompensas de World of Warcraft
(monturas, transfiguraciones, juguetes, mascotas...) en raids antiguas y rares.
Sustituye la hoja de cálculo con filtros combinables, fichas por objeto, notas propias
y progreso independiente para dos jugadores.

**Estado:** API, base de datos, frontend, tests de reset y despliegue listos.
El scraper de Wowhead está **aparcado a petición**; se añadirá en una fase posterior.

---

## 1. Estructura

```
app/
  main.py         API FastAPI + archivos estáticos
  config.py       constantes de reset y rutas (variables de entorno)
  resets.py       cálculo puro de periodos diarios/semanales en UTC
  db.py           conexiones SQLite y esquema idempotente
  repository.py   consultas y conversión a modelos Pydantic
  schemas.py      validación de entradas y salidas HTTP
static/           index.html, styles.css, app.js (JS vanilla, sin build)
tests/            test_resets.py, test_api.py
scripts/backup.sh backup con `sqlite3 .backup`
data/             volumen: tracker.db, images/, backups/  (no versionado)
```

`.opencode/` contiene las skills/commandos de [wheelhouse](https://github.com/bitIO/wheelhouse)
instalados en el proyecto; no son necesarios para ejecutar la aplicación.

---

## 2. Arranque local (Windows / Linux)

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
# Linux / macOS
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload
```

Abre <http://127.0.0.1:8000>. La documentación interactiva de la API está en
<http://127.0.0.1:8000/docs>.

Tests:

```bash
.\.venv\Scripts\python.exe -m pytest        # Windows
python -m pytest                            # Linux / macOS
```

---

## 3. Despliegue en Docker (VM Proxmox)

En la VM, dentro de la carpeta del proyecto:

```bash
mkdir -p data/images data/backups
# El contenedor corre con UID/GID 1000: el volumen debe ser escribible por ese usuario.
sudo chown -R 1000:1000 data
docker compose up -d --build
docker compose ps          # debe aparecer "healthy"
```

La web queda en `http://<IP-DE-LA-VM>:8000`. Ajusta el puerto en
`docker-compose.yml` si ya tienes algo escuchando ahí (tu red y acceso ya están
resueltos, así que no se añade proxy inverso).

Si ya has subido el proyecto a `/opt/wow-tracker`, `deploy.sh` hace todo lo anterior
(directorios, permisos, build, espera al healthcheck e instalación idempotente del
cron) en un solo paso:

```bash
cd /opt/wow-tracker
chmod +x deploy.sh
bash deploy.sh
```

El contenedor corre como usuario no root, usa una imagen Python 3.14 alpine fijada
por digest y monta `./data` en `/data`. Para reconstruir tras un cambio:

```bash
docker compose down
docker compose up -d --build
```

### Variables de entorno

| Variable | Valor por defecto | Significado |
| --- | --- | --- |
| `DATA_DIR` | `/data` en Docker | Directorio del volumen (db, imágenes, backups) |
| `DB_PATH` | `<DATA_DIR>/tracker.db` | Ruta exacta del fichero SQLite |
| `DAILY_RESET_HOUR_UTC` | `7` | Hora UTC del reset diario |
| `WEEKLY_RESET_WEEKDAY` | `2` | Día del reset semanal (0=lunes, 1=martes, 2=miércoles) |
| `WEEKLY_RESET_HOUR_UTC` | `7` | Hora UTC del reset semanal |
| `USER_1_NAME` / `USER_2_NAME` | `Jugador 1` / `Jugador 2` | Nombres iniciales (luego editables en la web) |

---

## 4. Cómo funciona el reinicio

No hay cron, ni tareas en segundo plano, ni columna "reset"). Al leer cada objeto
se calcula el inicio del periodo vigente:

- **Diario:** último `07:00 UTC` ya pasado.
- **Semanal:** último **miércoles 07:00 UTC** ya pasado (reset EU).

Un registro de progreso cuenta como *hecho* solo si su `last_done_at` es posterior
o igual al inicio del periodo actual. Por eso un objeto marcado el lunes pasa a
pendiente el miércoles por la mañana sin que nadie escriba en la base de datos.
Todo se calcula en UTC con `zoneinfo`; los cambios de horario de verano no afectan.
Las tres constantes se pueden sobreescribir por entorno (tabla anterior).

Cada usuario tiene su propia fila en `progress` con `last_done_at`, así que marcar
algo no cambia el estado del otro jugador.

---

## 5. Uso de la interfaz

- El **selector "Visto por"** elige qué jugador se ve; se recuerda en el navegador.
- Los **filtros** (tipo, periodicidad, fuente, tipo de fuente, estado y texto) se
  combinan entre sí; el botón **Limpiar** los resetea.
- Clic en una fila abre la **ficha**: descripción, relaciones N:M de fuentes con
  drop rate/dificultad/condiciones (editables desde la propia ficha), notas
  editables, enlace a Wowhead, último `last_done_at`, próximo reset y fecha del
  último scraping.
- **Marcar hecho** guarda `last_done_at` para el jugador elegido.
- **+ Añadir objeto** crea el objeto con sus fuentes. El campo **Para quién** permite
  asignarlo a **Ambos** (valor por defecto), a Jugador 1 o a Jugador 2. Si falta una
  fuente, se puede crear desde el propio formulario. El icono local se guarda como
  nombre de archivo en `data/images/` y se sirve desde `/media/<nombre>` (también
  admite URL).
- El botón **✎ Editar** de la ficha abre el formulario completo para cambiar nombre,
  Wowhead ID/URL, tipo, periodicidad, «Para quién», icono, descripción y notas. Las
  fuentes y su drop rate/dificultad/condiciones se siguen editando en la propia ficha.
- Los objetos asignados a otro jugador **siguen apareciendo en el listado**, pero se
  muestran atenuados con la etiqueta «Solo para X». Su ficha es de **solo lectura**:
  no se pueden editar drop rate/dificultad/condiciones ni notas, y el botón de
  progreso está deshabilitado. La API también rechaza marcarlos con HTTP `403`.

Modelo de datos: `items` (con `owner_user_id` nullable; `NULL` = ambos), `sources`,
`item_sources` (N:M con drop rate, dificultad y condiciones), `users` y `progress`
(clave primaria `item_id` + `user_id`).

Ejemplo rápido por API:

```bash
curl -X POST http://127.0.0.1:8000/api/sources \
  -H 'Content-Type: application/json' \
  -d '{"name":"Sagrario de dominación","kind":"raid","zone":"Sagrario de dominación","wowhead_url":"https://www.wowhead.com/es/zone=13561"}'

curl -X POST http://127.0.0.1:8000/api/items \
  -H 'Content-Type: application/json' \
  -d '{"name":"Riendas de la venganza","wowhead_id":137539,"type":"mount","periodicity":"weekly","sources":[{"source_id":1,"drop_rate":10,"difficulty":"Mítico","conditions":"Rare drop"}]}'
```

`owner_user_id` es opcional: omítelo (o envía `null`) para "Ambos", o envía el
`id` devuelto por `/api/meta` para asignarlo a un jugador concreto.

---

## 6. Backups

`scripts/backup.sh` usa **`sqlite3 .backup`**, que genera una copia coherente
mientras la base está en uso; no copia el fichero `.db` en caliente. Comprime el
resultado en `data/backups/tracker_AAAAmmdd_HHMMSS.db.gz` y borra copias de más
de 30 días (`RETENTION_DAYS`).

Manual:

```bash
docker compose exec -T wow-tracker /app/scripts/backup.sh
```

**cron en la VM Proxmox** (copia diaria a las 04:00). Edita con `crontab -e`:

```cron
0 4 * * * docker compose -f /opt/wow-tracker/docker-compose.yml exec -T wow-tracker /app/scripts/backup.sh >> /var/log/wow-tracker-backup.log 2>&1
```

Ajusta la ruta `/opt/wow-tracker` a donde hayas clonado el proyecto. El usuario
que ejecuta cron debe poder llamar a `docker compose`.

Para restaurar:

```bash
docker compose stop wow-tracker
gunzip -c data/backups/tracker_AAAAmmdd_HHMMSS.db.gz > data/tracker.db
docker compose start wow-tracker
```

Si prefieres lanzar el cron desde el host con `sqlite3` instalado:

```cron
0 4 * * * DATA_DIR=/opt/wow-tracker/data /opt/wow-tracker/scripts/backup.sh >> /var/log/wow-tracker-backup.log 2>&1
```

---

## 7. Pendiente

- Scraper de Wowhead: descarga y caché del HTML, parser con datos reales (objeto de
  ejemplo: Riendas de la venganza, `wowhead_id` 137539) y tests con HTML cacheado.
- Importación desde el Excel actual.
