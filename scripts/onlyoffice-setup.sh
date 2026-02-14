#!/bin/bash
# ============================================================
# ONLYOFFICE Document Server - Docker Setup
# Startet den ONLYOFFICE Document Server als Docker-Container
# mit JWT-Authentifizierung und WOPI-Unterstuetzung.
#
# Laeuft nicht-interaktiv (fuer apt postinst geeignet).
# Ermittelt Server-IP automatisch und speichert die Config
# direkt in die Tareas-Datenbank.
#
# Docker wird als apt-Dependency automatisch mitinstalliert.
# ============================================================

set -e

CONTAINER_NAME="onlyoffice-docs"
PORT=8090
APP_DIR="${APP_DIR:-/opt/tareas}"
DB_PATH="$APP_DIR/data/tareas.db"
SERVER_IP=$(hostname -I | awk '{print $1}')

info()  { echo "[ONLYOFFICE] $1"; }

save_config_to_db() {
    local server_url="http://${SERVER_IP}:${PORT}"
    if [ -f "$DB_PATH" ]; then
        # SQL-Injection verhindern: Single-Quotes escapen (' -> '')
        local safe_url="${server_url//\'/\'\'}"
        local safe_secret="${JWT_SECRET//\'/\'\'}"
        sqlite3 "$DB_PATH" "INSERT OR REPLACE INTO onlyoffice_config (id, server_url, jwt_secret) VALUES (1, '${safe_url}', '${safe_secret}');"
        info "Config in DB gespeichert: ${server_url}"
    fi
}

# Docker-Daemon muss laufen
if ! docker info &> /dev/null; then
    info "Docker-Daemon starten..."
    systemctl start docker.service 2>/dev/null || true
    sleep 2
    if ! docker info &> /dev/null; then
        info "FEHLER: Docker-Daemon nicht erreichbar."
        exit 1
    fi
fi

# JWT Secret: bestehenden Container wiederverwenden oder neu generieren
JWT_SECRET=""
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    # Container existiert - JWT Secret aus Env auslesen
    JWT_SECRET=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER_NAME" 2>/dev/null | grep "^JWT_SECRET=" | cut -d= -f2)

    # Container laeuft bereits und ist gesund?
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        if curl -sf "http://localhost:${PORT}/healthcheck" > /dev/null 2>&1; then
            info "Container '${CONTAINER_NAME}' laeuft bereits und ist gesund."
            if [ -n "$JWT_SECRET" ]; then
                save_config_to_db
            fi
            exit 0
        fi
    fi

    # Container existiert aber laeuft nicht oder ist nicht gesund -> neu erstellen
    info "Ersetze bestehenden Container..."
    docker stop "${CONTAINER_NAME}" 2>/dev/null || true
    docker rm "${CONTAINER_NAME}" 2>/dev/null || true
fi

# Neues JWT Secret falls keines vorhanden
if [ -z "$JWT_SECRET" ]; then
    JWT_SECRET=$(openssl rand -hex 32)
fi

info "Server-IP: ${SERVER_IP}"
info "Port: ${PORT}"

# Container starten
info "Starte ONLYOFFICE Document Server..."
docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    -p "${PORT}:80" \
    -e JWT_ENABLED=true \
    -e JWT_SECRET="${JWT_SECRET}" \
    -e WOPI_ENABLED=true \
    onlyoffice/documentserver

# Healthcheck (max 120 Sekunden)
info "Warte auf Healthcheck..."
MAX_WAIT=120
WAITED=0
HEALTHY=false
while [ $WAITED -lt $MAX_WAIT ]; do
    if curl -sf "http://localhost:${PORT}/healthcheck" > /dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    sleep 5
    WAITED=$((WAITED + 5))
    info "Warte... (${WAITED}s / ${MAX_WAIT}s)"
done

if [ "$HEALTHY" = true ]; then
    info "ONLYOFFICE Document Server ist bereit!"
else
    info "WARNUNG: Healthcheck nach ${MAX_WAIT}s nicht erfolgreich."
    info "Container startet moeglicherweise noch im Hintergrund."
fi

# Config in Tareas-DB speichern
save_config_to_db

info "Setup abgeschlossen."
