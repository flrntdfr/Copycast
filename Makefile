.DEFAULT_GOAL := help

.PHONY: help build serve serve-prod test clean docker-build docker-up docker-down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

build: ## Build the production jar (includes the Vaadin frontend bundle)
	mvn -Pproduction -DskipTests package

serve: ## Run in dev mode with hot reload (http://localhost:8080)
	mvn spring-boot:run

serve-prod: build ## Build and run the production jar
	java -jar target/copycast-*.jar

test: ## Run unit tests
	mvn test

clean: ## Remove build artifacts
	mvn clean

docker-build: ## Build the Docker image
	docker build -t copycast .

docker-up: ## Build and start via docker compose
	docker compose up --build -d

docker-down: ## Stop the docker compose stack
	docker compose down
