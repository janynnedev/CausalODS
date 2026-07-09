FROM mcr.microsoft.com/devcontainers/java:21

# Dependências básicas
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    unzip \
    python3 \
    python3-pip \
    maven \
    ca-certificates \
    gnupg \
    lsb-release

# Instala Docker CLI (não instala o daemon)
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/debian \
      $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
      > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli

WORKDIR /workspaces/obs-platform