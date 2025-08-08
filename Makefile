CLEANFILES=	*.json


all:

container:
	docker build --pull -t scoutnet2google .

test:
	uv run scoutnet2google

lint:
	uv run ruff check

clean:
	rm -f $(CLEANFILES)
