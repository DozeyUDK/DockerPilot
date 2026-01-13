# Dockerfile for Grafana
# Based on configuration from grafana container
# Base image: grafana/grafana:latest

FROM grafana/grafana:latest

# Image information
LABEL maintainer="DockerPilot"
LABEL description="Grafana container based on current running container configuration"
LABEL version="1.0"

# User settings (Grafana uses grafana user)
USER grafana

# Environment variables from container configuration
# WARNING: In production use secrets or environment variables instead of hardcoding passwords!
ENV GF_SECURITY_ADMIN_USER=admin
ENV GF_SECURITY_ADMIN_PASSWORD=admin
ENV GF_SERVER_ROOT_URL=http://localhost:3000
ENV GF_INSTALL_PLUGINS=""

# Grafana paths (standard, already set in base image)
# GF_PATHS_CONFIG=/etc/grafana/grafana.ini
# GF_PATHS_DATA=/var/lib/grafana
# GF_PATHS_HOME=/usr/share/grafana
# GF_PATHS_LOGS=/var/log/grafana
# GF_PATHS_PLUGINS=/var/lib/grafana/plugins
# GF_PATHS_PROVISIONING=/etc/grafana/provisioning

# Grafana port
EXPOSE 3000

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=40s \
  CMD wget --no-verbose --tries=1 --spider http://localhost:3000/api/health || exit 1

# Grafana startup (default command from base image)
# Grafana automatically starts via /run.sh
CMD ["/run.sh"]

