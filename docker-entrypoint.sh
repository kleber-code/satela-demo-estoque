#!/bin/sh
# Na plataforma Satela o Procfile (release) já roda migrate + seed; aqui repetimos
# de forma idempotente para o caso de a imagem ser executada isoladamente.
set -e
python manage.py migrate --noinput
case "${DEMO_MODE:-1}" in
  0|false|False|no|off) ;;
  *) python manage.py seed_demo --se-vazio ;;
esac
exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 2
