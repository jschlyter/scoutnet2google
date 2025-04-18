CLEANFILES=	*.json


all:

container:
	docker build --pull -t scoutnet2google .

test:
	uv run python scoutnet2google.py

lint:
	uv run ruff check

clean:
	rm -f $(CLEANFILES)
