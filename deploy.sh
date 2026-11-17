#!/usr/bin/env bash
# One-shot deployment helper for the Proxmox VM.
# Run from the project directory: bash deploy.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DATA_DIR="$PROJECT_DIR/data"
CONTAINER="wow-tracker"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

echo "==> Proyecto: $PROJECT_DIR"
cd "$PROJECT_DIR"

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker no está instalado." >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: falta el plugin docker-compose-plugin." >&2
    exit 1
fi
if [ ! -f "$COMPOSE_FILE" ]; then
    echo "ERROR: no se encontró $COMPOSE_FILE" >&2
    exit 1
fi

echo "==> Preparando $DATA_DIR"
mkdir -p "$DATA_DIR/images" "$DATA_DIR/backups"

# El contenedor corre con UID/GID 1000; el volumen debe ser escribible por ese usuario.
if [ "$(id -u)" -eq 0 ]; then
    chown -R 1000:1000 "$DATA_DIR"
elif command -v sudo >/dev/null 2>&1; then
    sudo chown -R 1000:1000 "$DATA_DIR"
else
    echo "AVISO: no se pudo ajustar el propietario de data/ a 1000:1000." >&2
    echo "       Ejecuta: sudo chown -R 1000:1000 $DATA_DIR" >&2
fi

echo "==> Construyendo y arrancando el contenedor"
docker compose -f "$COMPOSE_FILE" up -d --build

echo "==> Esperando al healthcheck"
status="unknown"
for _ in $(seq 1 30); do
    status="$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo unknown)"
    if [ "$status" = "healthy" ]; then
        break
    fi
    if [ "$status" = "unhealthy" ]; then
        echo "ERROR: el contenedor está unhealthy. Últimos logs:" >&2
        docker compose -f "$COMPOSE_FILE" logs --tail=50 >&2
        exit 1
    fi
    sleep 2
done

if [ "$status" != "healthy" ]; then
    echo "ERROR: el contenedor no alcanzó el estado healthy a tiempo." >&2
    docker compose -f "$COMPOSE_FILE" logs --tail=50 >&2
    exit 1
fi

echo "==> Instalando el backup en cron (diario 04:00)"
CRON_MARK="$PROJECT_DIR/docker-compose.yml exec -T $CONTAINER"
CRON_LINE="0 4 * * * /usr/bin/docker compose -f $PROJECT_DIR/docker-compose.yml exec -T $CONTAINER /app/scripts/backup.sh >> /var/log/wow-tracker-backup.log 2>&1"

crontab_cmd() {
    if [ "$(id -u)" -eq 0 ]; then
        crontab "$@"
    else
        sudo crontab "$@"
    fi
}

if ! command -v crontab >/dev/null 2>&1; then
    echo "AVISO: crontab no disponible; añade esta línea manualmente:" >&2
    echo "$CRON_LINE" >&2
elif crontab_cmd -l 2>/dev/null | grep -Fq "$CRON_MARK"; then
    echo "    Ya estaba instalado."
else
    { crontab_cmd -l 2>/dev/null || true; echo "$CRON_LINE"; } | crontab_cmd -
    echo "    Cron instalado."
fi

echo
echo "==> Despliegue completado"
echo "    Web: http://<IP-DE-LA-VM>:8000"
echo "    Health: $(curl -fsS http://127.0.0.1:8000/health 2>/dev/null || echo 'no disponible desde este host')"
echo "    Datos: $DATA_DIR"
echo "    Backups: $DATA_DIR/backups"
