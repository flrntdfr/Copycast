# ---- Build stage ----------------------------------------------------------
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
COPY pom.xml ./
# Warm the dependency cache; tolerate partial resolution offline quirks.
RUN mvn -q -Pproduction dependency:go-offline || true
COPY src ./src
RUN mvn -q -Pproduction -DskipTests package

# ---- Runtime stage --------------------------------------------------------
FROM eclipse-temurin:21-jre
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /build/target/copycast-*.jar app.jar

# All options live in /config/application.yaml (see config/ in the repo).
# The data dir is the container convention; everything else stays in config.
ENV SPRING_CONFIG_ADDITIONAL_LOCATION=optional:file:/config/ \
    COPYCAST_DATADIR=/data

VOLUME ["/data", "/config"]
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
