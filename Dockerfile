FROM python:3.12-slim
WORKDIR /srv/finder_fee
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV FF_ENV=production FF_COOKIE_SECURE=1 FLASK_APP=wsgi:app
VOLUME ["/srv/finder_fee/instance"]
EXPOSE 8000
CMD ["sh", "-c", "flask init-db && gunicorn -w 2 -b 0.0.0.0:8000 wsgi:app"]
