lint:
	uv run black ./src/infra_visualiser_action/
	uv run flake8 ./src/infra_visualiser_action/

docker:
	docker build -t infra-visualiser-action .

test:
	uv run pytest tests/
