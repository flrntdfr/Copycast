# ---- Build stage ----------------------------------------------------------
# Runs on the build host's architecture: the jar is arch-independent, so
# multi-arch image builds don't need to run Maven under emulation.
FROM --platform=$BUILDPLATFORM maven:3-eclipse-temurin-26 AS build
WORKDIR /build
COPY pom.xml ./
# Warm the dependency cache; tolerate partial resolution offline quirks.
RUN mvn -q -Pproduction dependency:go-offline || true
COPY src ./src
RUN mvn -q -Pproduction -DskipTests package

# ---- yt-dlp stage ---------------------------------------------------------
# Bakes the pinned yt-dlp binary into the image. The version is NOT declared
# here: it is grepped from application.yaml, the single source of truth
# (see docs/adr/0002).
# Runs on the build host too — it only downloads; TARGETARCH picks the asset.
FROM --platform=$BUILDPLATFORM eclipse-temurin:21-jre AS ytdlp
ARG TARGETARCH
COPY src/main/resources/application.yaml /tmp/application.yaml
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && YTDLP_VERSION=$(awk '/ytdlp:/{f=1} f && /version:/{gsub(/[" ]/,"",$2); print $2; exit}' /tmp/application.yaml) \
    && case "${TARGETARCH:-amd64}" in \
         arm64) ASSET=yt-dlp_linux_aarch64 ;; \
         *)     ASSET=yt-dlp_linux ;; \
       esac \
    && mkdir -p /opt/copycast/bin \
    && curl -fsSL -o "/opt/copycast/bin/yt-dlp-${YTDLP_VERSION}" \
         "https://github.com/yt-dlp/yt-dlp/releases/download/${YTDLP_VERSION}/${ASSET}" \
    && chmod +x "/opt/copycast/bin/yt-dlp-${YTDLP_VERSION}"

# ---- Runtime stage --------------------------------------------------------
FROM eclipse-temurin:21-jre
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=ytdlp /opt/copycast/bin /opt/copycast/bin
COPY --from=build /build/target/copycast-*.jar app.jar

# All options live in /config/application.yaml (see config/ in the repo).
# The data dir is the container convention; everything else stays in config.
ENV SPRING_CONFIG_ADDITIONAL_LOCATION=optional:file:/config/ \
    COPYCAST_DATADIR=/data

VOLUME ["/data", "/config"]
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
