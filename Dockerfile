FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY pytest.ini .
COPY alembic ./alembic
COPY app ./app
COPY tests ./tests

ENV PYTHONUNBUFFERED=1
ENV APP_ENV=local
ENV LOCAL_DATA_DIR=/app/local-data

RUN mkdir -p /app/local-data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
