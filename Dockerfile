# Dockerfile dla Grafana
# Bazowany na konfiguracji z kontenera grafana
# Obraz bazowy: grafana/grafana:latest

FROM grafana/grafana:latest

# Informacje o obrazie
LABEL maintainer="DockerPilot"
LABEL description="Grafana container based on current running container configuration"
LABEL version="1.0"

# Ustawienia użytkownika (Grafana używa użytkownika grafana)
USER grafana

# Zmienne środowiskowe z konfiguracji kontenera
# UWAGA: W produkcji użyj secrets lub environment variables zamiast hardcodowania haseł!
ENV GF_SECURITY_ADMIN_USER=admin
ENV GF_SECURITY_ADMIN_PASSWORD=admin
ENV GF_SERVER_ROOT_URL=http://localhost:3000
ENV GF_INSTALL_PLUGINS=""

# Ścieżki Grafany (standardowe, już ustawione w obrazie bazowym)
# GF_PATHS_CONFIG=/etc/grafana/grafana.ini
# GF_PATHS_DATA=/var/lib/grafana
# GF_PATHS_HOME=/usr/share/grafana
# GF_PATHS_LOGS=/var/log/grafana
# GF_PATHS_PLUGINS=/var/lib/grafana/plugins
# GF_PATHS_PROVISIONING=/etc/grafana/provisioning

# Port Grafany
EXPOSE 3000

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
  CMD wget --no-verbose --tries=1 --spider http://localhost:3000/api/health || exit 1

# Uruchomienie Grafany (domyślne polecenie z obrazu bazowego)
# Grafana automatycznie uruchamia się przez /run.sh
CMD ["/run.sh"]

