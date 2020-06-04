CLEANFILES=	*.json


all:

container:
	docker build --pull -t scoutnet2google .

test:
	python3 scoutnet2google.py

lint:
	pylama *.py

clean:
	rm -f $(CLEANFILES)
