FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/finder_fee

# Versões exatas, conferidas pelo hash (requirements.lock; ver scripts/atualizar-dependencias.sh).
COPY requirements.lock .
RUN pip install --require-hashes -r requirements.lock

COPY . .
# O app roda sem root. O código fica do root (só leitura para o app); o usuário do app só escreve em instance/.
RUN useradd --system --uid 10001 --home-dir /srv/finder_fee --shell /usr/sbin/nologin finder \
    && mkdir -p instance && chown -R finder:finder instance
USER finder

ENV FF_ENV=production FF_COOKIE_SECURE=1 FLASK_APP=wsgi:app
VOLUME ["/srv/finder_fee/instance"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/saude', timeout=4)"
# init-db aplica as migrações pendentes a cada subida; exec deixa o gunicorn receber o sinal de parada.
# FF_BIND: com --network host, use 127.0.0.1:8000 para só o nginx alcançar o app. A porta fica 8000 (HEALTHCHECK).
CMD ["sh", "-c", "flask init-db && exec gunicorn -w ${FF_WORKERS:-2} -b ${FF_BIND:-0.0.0.0:8000} --no-control-socket --worker-tmp-dir /dev/shm --access-logfile - wsgi:app"]
