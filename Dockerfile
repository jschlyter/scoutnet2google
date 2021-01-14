FROM python:3.8

WORKDIR /tmp
COPY dist/*.whl .
RUN pip install *.whl
RUN rm *.whl

WORKDIR /scout/conf
CMD [ "scoutnet2google" ]
