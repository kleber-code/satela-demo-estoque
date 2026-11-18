FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Usuário não root; /app precisa ser gravável (chave secreta gerada, sqlite local, media).
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app \
    && chmod +x docker-entrypoint.sh

# collectstatic no build (a chave abaixo vale apenas durante o build).
RUN DJANGO_SECRET_KEY=build-only-nao-usada-em-runtime-0123456789-0123456789-0123456789 \
    python manage.py collectstatic --noinput

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/saude/' % os.environ.get('PORT','8000'), timeout=4)"

CMD ["./docker-entrypoint.sh"]
