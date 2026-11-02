# BugBait, ready to host anywhere that runs containers
# (Render, Fly.io, Railway, GCP Cloud Run, AWS, ...).
#
# Build:  docker build -t bugbait .
# Run:    docker run -p 5050:5050 -e META_API_KEY=... [-e GITHUB_TOKEN=...] bugbait
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# git (clones) + node (JavaScript) + JDK/Maven (Java)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates nodejs npm openjdk-17-jdk-headless maven \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV HOST=0.0.0.0 PORT=5050
EXPOSE 5050
CMD ["python", "app.py"]
