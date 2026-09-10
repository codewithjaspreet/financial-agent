.PHONY: setup eval test run ui seed-full

setup:
	docker-compose up -d
	@echo "Waiting for Postgres..."
	@until docker-compose exec -T postgres pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
	@test -f .env || cp .env.example .env
	uv sync
	uv run alembic upgrade head
	uv run python -m app.scripts.seed --demo
	@echo ""
	@echo "Ready."
	@echo "  API   uv run uvicorn main:app --reload --port 8000"
	@echo "  UI    uv run streamlit run frontend/app.py"
	@echo "  Eval  make eval"
	@echo "  Login demo@tenant-a.test / demo-password-123"

eval:
	uv run python -m eval.run

test:
	uv run pytest app/tests -q

run:
	uv run uvicorn main:app --reload --port 8000

ui:
	uv run streamlit run frontend/app.py

seed-full:
	uv run python -m app.scripts.seed --full
