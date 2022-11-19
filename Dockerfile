FROM python:3.10 AS builder
RUN pip3 install poetry
WORKDIR /tmp
COPY pyproject.toml poetry.lock *.py /tmp/
RUN poetry build

FROM python:3.10
WORKDIR /scout
COPY --from=builder /tmp/dist/*.whl .
RUN pip install *.whl
RUN rm *.whl
WORKDIR /scout/conf
ENTRYPOINT scoutnet2google
