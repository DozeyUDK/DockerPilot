FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# The upstream image currently carries a Yarn APT source whose signing key can
# rotate independently and break Dev Container Features that run apt-get update.
# DockerPilot gets Node from the Node Dev Container Feature, so this repository
# is not required for the demo image.
RUN rm -f \
    /etc/apt/sources.list.d/yarn.list \
    /etc/apt/sources.list.d/yarn.sources
