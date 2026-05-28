# Mintel Production Deploy

Mintel runs as an independent FastAPI service with its own Postgres database.

## Files

- `docker-compose.prod.yml`: production app and Postgres stack.
- `.env.prod.example`: copy to `.env.prod` and replace all secrets.
- `deploy/deploy-prod.sh`: build, migrate, start, and health-check.
- `deploy/nginx/mintel.conf`: nginx reverse proxy example for `mintel.midhtech.in`.

## First Deploy

```bash
sudo mkdir -p /proj/app/mintel
sudo chown "$USER":"$USER" /proj/app/mintel
rsync -a --delete ./ /proj/app/mintel/
cd /proj/app/mintel
cp .env.prod.example .env.prod
```

Edit `.env.prod`:

- Set a long random `SECRET_KEY`.
- Set a strong `POSTGRES_PASSWORD`.
- Make `DATABASE_URL` use the same database password.
- Set `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD`.
- Set `ALLOWED_HOSTS` to the production hostname.
- Set `MAAS_BASE_URL` to the production MAAS URL.

Then start:

```bash
./deploy/deploy-prod.sh
```

The container entrypoint runs `alembic upgrade head` before Uvicorn starts.

## Nginx

Copy `deploy/nginx/mintel.conf` into nginx sites and enable TLS with certbot:

```bash
sudo cp deploy/nginx/mintel.conf /etc/nginx/sites-available/mintel.conf
sudo ln -sf /etc/nginx/sites-available/mintel.conf /etc/nginx/sites-enabled/mintel.conf
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d mintel.midhtech.in
```

## Health Check

```bash
curl --fail http://127.0.0.1:8009/health
```

Expected:

```json
{"status":"ok","service":"Mintel"}
```
