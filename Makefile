CLEANFILES=	*.json


all:

test:
	python3 scoutnet2google.py

lint:
	pylama *.py

clean:
	rm -f $(CLEANFILES)

tidy:
	rm -f scoutnet-*.json
