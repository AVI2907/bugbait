# BugBait, ready to host anywhere that runs containers
# (Render, Fly.io, Railway, GCP Cloud Run, AWS, ...).
#
# Build:  docker build -t bugbait .
# Run:    docker run -p 5050:5050 -e META_API_KEY=... [-e GITHUB_TOKEN=...] bugbait
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# git (clones) + node (JavaScript) + JDK/Maven (Java) + Go + Rust + Ruby + PHP + Kotlin.
# default-jdk-headless follows the distro default (trixie ships 21; plain "17" doesn't exist there).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates unzip nodejs npm default-jdk-headless maven golang cargo rustc rustfmt \
        ruby php phpunit \
    && rm -rf /var/lib/apt/lists/*
# Kotlin compiler (kotlinc) from the official release.
ARG KOTLIN_VERSION=2.1.21
RUN curl -sSL -o /tmp/kotlin.zip \
        https://github.com/JetBrains/kotlin/releases/download/v${KOTLIN_VERSION}/kotlin-compiler-${KOTLIN_VERSION}.zip \
    && unzip -q /tmp/kotlin.zip -d /opt && rm /tmp/kotlin.zip \
    && ln -s /opt/kotlinc/bin/kotlinc /usr/local/bin/kotlinc
RUN gem install --no-document minitest rspec
# .NET SDK (C#) from Microsoft's feed (Debian 13 / trixie).
RUN curl -sSL -o /tmp/ms-prod.deb \
        https://packages.microsoft.com/config/debian/13/packages-microsoft-prod.deb \
    && dpkg -i /tmp/ms-prod.deb && rm /tmp/ms-prod.deb \
    && apt-get update && apt-get install -y --no-install-recommends dotnet-sdk-9.0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV HOST=0.0.0.0 PORT=5050
EXPOSE 5050
CMD ["python", "app.py"]
