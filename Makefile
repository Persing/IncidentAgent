.PHONY: ingest eval eval-classify api

ingest:
	python -m src.ingestion.loader

eval:
	python -m src.evaluation.eval

eval-classify:
	python -m src.evaluation.eval --classify

api:
	uvicorn src.api.main:app --port 8000
