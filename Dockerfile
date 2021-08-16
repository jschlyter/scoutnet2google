FROM python:3.9

WORKDIR /tmp
COPY dist/*.whl .
RUN pip install *.whl
RUN rm *.whl

WORKDIR /scout/conf
CMD [ "scoutnet2google" ]
