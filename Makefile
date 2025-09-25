# Makefile for FA Service Core

.PHONY: help install test lint format migrate migrate-create migrate-upgrade migrate-downgrade migrate-reset migrate-init clean dev-setup

# Default target
help:
	@echo "Available targets:"
	@echo "  install          Install dependencies"
	@echo "  test             Run tests"
	@echo "  lint             Run linting (ruff + mypy)"
	@echo "  format           Format code (black + isort)"
	@echo "  migrate-create   Create new migration (usage: make migrate-create MESSAGE='your message')"
	@echo "  migrate-upgrade  Upgrade database to latest migration"
	@echo "  migrate-downgrade Downgrade database by one migration"
	@echo "  migrate-current  Show current migration"
	@echo "  migrate-history  Show migration history"
	@echo "  migrate-reset    Reset database (development only)"
	@echo "  migrate-init     Initialize database with full setup"
	@echo "  dev-setup        Set up development environment"
	@echo "  clean            Clean up generated files"

# Installation
install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

# Testing
test:
	pytest tests/ -v

test-cov:
	pytest tests/ --cov=core --cov-report=html --cov-report=term

# Code quality
lint:
	ruff check .
	mypy core/

format:
	black .
	isort .
	ruff check --fix .

# Database migrations
migrate-create:
	@if [ -z "$(MESSAGE)" ]; then \
		echo "Usage: make migrate-create MESSAGE='your migration message'"; \
		exit 1; \
	fi
	python -m core.migrations create "$(MESSAGE)"

migrate-upgrade:
	python -m core.migrations upgrade

migrate-downgrade:
	python -m core.migrations downgrade

migrate-current:
	python -m core.migrations current

migrate-history:
	python -m core.migrations history

migrate-reset:
	@echo "WARNING: This will reset the database and destroy all data!"
	@read -p "Are you sure? (y/N): " confirm && [ "$$confirm" = "y" ]
	python -m core.migrations reset

migrate-init:
	python -m core.migrations init

# Development setup
dev-setup: install-dev
	@echo "Setting up development environment..."
	@echo "Creating .env file from example..."
	@if [ ! -f .env ]; then cp env.example .env; fi
	@echo "Installing pre-commit hooks..."
	pre-commit install
	@echo "Development environment ready!"

# Docker operations
docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f

# Clean up
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf build/
	rm -rf dist/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/

# Run services (for development)
run-sites:
	cd services/sites && python main.py

run-pages:
	cd services/pages && python main.py

run-pages-query:
	cd services/pages_query && python main.py

# Full development cycle
dev-cycle: format lint test

# CI/CD helpers
ci-install:
	pip install -e ".[dev]"

ci-test: lint test

# Backup and restore (for development)
db-backup:
	@echo "Creating database backup..."
	pg_dump $(DATABASE_WRITE_URL) > backup_$$(date +%Y%m%d_%H%M%S).sql

db-restore:
	@if [ -z "$(BACKUP_FILE)" ]; then \
		echo "Usage: make db-restore BACKUP_FILE=backup_file.sql"; \
		exit 1; \
	fi
	psql $(DATABASE_WRITE_URL) < $(BACKUP_FILE)

# Health checks
health-check:
	@echo "Checking database connectivity..."
	@python -c "import asyncio; from core.db import init_database, check_database_connection; \
	import os; \
	async def check(): \
		db = init_database(os.getenv('DATABASE_WRITE_URL'), os.getenv('DATABASE_READ_URL')); \
		try: \
			result = await check_database_connection(db.write_engine); \
			print('Write DB:', result['status']); \
			result = await check_database_connection(db.read_engine); \
			print('Read DB:', result['status']); \
		finally: \
			await db.close(); \
	asyncio.run(check())"

# Metrics functionality removed - using structured logging instead

# Example data loading
load-example-data:
	python scripts/load_example_data.py

# Documentation
docs-build:
	@echo "Building documentation..."
	# Add your documentation build command here

docs-serve:
	@echo "Serving documentation..."
	# Add your documentation serve command here
