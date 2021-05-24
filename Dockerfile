FROM python:3.9

WORKDIR /scout
COPY dist/*.whl .
RUN pip install *.whl
RUN rm *.whl
WORKDIR /scout/conf
CMD [ "python", "/scout/scoutnet2google.py" ]
