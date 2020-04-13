FROM python:3.8

WORKDIR /scout
COPY scoutnet2google.py ./
COPY requirements.txt ./
RUN pip install -r requirements.txt
WORKDIR /scout/conf
CMD [ "python", "/scout/scoutnet2google.py" ]
