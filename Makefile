.PHONY: install run batch dashboard ollama-setup clean

install:
	python -m pip install --upgrade pip
	python -m pip install -r requirements.txt

run:
	python -m src.main --url "$(URL)"

batch:
	python -m src.main --batch sources.txt

dashboard:
	python -m uvicorn dashboard.app:app --reload --host 0.0.0.0 --port 8000

ollama-setup:
	ollama pull qwen2.5:7b

clean:
	rm -rf output/raw/* output/clips/* output/final/*
