.PHONY: install dev test judge docker-build docker-run smoke

install:
	python3 -m pip install -r requirements-dev.txt

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	python3 -m pytest -q

judge:
	python3 -m harness.judge --base-url $${BASE_URL:-http://localhost:8000}

smoke:
	bash scripts/smoke.sh $${BASE_URL:-http://localhost:8000}

docker-build:
	docker build -t gridwise-llm:local .

docker-run:
	docker run --rm -p 8000:8000 --env-file .env gridwise-llm:local
