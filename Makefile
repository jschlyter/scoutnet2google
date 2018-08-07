CLEANFILES=	*.json


all:

test:
	python3 scoutnet2google.py

lint:
	pylama

clean:
	rm -f $(CLEANFILES)
