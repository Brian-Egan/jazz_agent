.PHONY: up down migrate test lint typecheck project-instructions

up:
	docker compose up -d db

down:
	docker compose down

migrate:
	uv run python scripts/migrate.py

project-instructions:
	uv run python scripts/generate_project_instructions.py

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy src
