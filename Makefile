CLEANFILES=	*.json


all:

container:
	docker build --pull -t scoutnet2google .

test:
	poetry run scoutnet2google.py

lint:
	poetry run pylama *.py

clean:
	rm -f $(CLEANFILES)
