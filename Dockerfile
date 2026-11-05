FROM golang:1.26.2-bookworm AS provider
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
WORKDIR /src/provider
COPY provider/ ./
ARG TARGETARCH=amd64
RUN bash build.sh /music-provider

FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=provider /music-provider /usr/local/bin/music-provider
COPY provider/ /usr/share/music-gateway/provider-source/
COPY LICENSE THIRD-PARTY.md /usr/share/music-gateway/
COPY gateway.py publication.py quality.py ./
ENV PYTHONUNBUFFERED=1
USER 1000:1000
EXPOSE 4534
CMD ["python", "/app/gateway.py"]
