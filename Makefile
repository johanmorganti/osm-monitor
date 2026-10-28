.PHONY: venv install css run migrate shell clean

venv:
	python -m venv venv && source venv/bin/activate

install:
	pip install -r requirements.txt

# The Docker image builds this itself; outside Docker, put the Tailwind v3.4.17 standalone CLI
# (the release the Dockerfile pins) at ./tailwindcss first.
css:
	./tailwindcss -c tailwind/tailwind.config.js -i tailwind/input.css -o static/css/site.css --minify

run:
	python manage.py runserver

migrate:
	python manage.py makemigrations
	python manage.py migrate

shell:
	python manage.py shell

clean:
	find . -type d -name "__pycache__" -exec rm -r {} +
	find . -type f -name "*.pyc" -delete

setup: venv install migrate

.DEFAULT_GOAL := help
help:
	@echo "Available commands:"
	@echo "  make venv      - Create a new virtual environment"
	@echo "  make install   - Install project dependencies"
	@echo "  make css       - Build static/css/site.css (Tailwind; needs ./tailwindcss)"
	@echo "  make run      - Run the development server"
	@echo "  make migrate   - Run database migrations"
	@echo "  make shell    - Open Django shell"
	@echo "  make clean    - Remove Python cache files"
	@echo "  make setup    - Full setup: create venv, install deps, and migrate" 