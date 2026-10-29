### Run locally

Postgres, igual que staging y producción. No hace falta `.env`: el stack local trae sus propias credenciales fijas (`postgres`/`postgres`).

```bash
docker compose -f docker-compose.local.yml up --build
```

`http://localhost:8000` — Django corre ahí directo, sin nginx. Migraciones corren solas antes de levantar gunicorn.

```bash
# comandos de manage.py sueltos
docker compose -f docker-compose.local.yml exec web python manage.py createsuperuser
docker compose -f docker-compose.local.yml exec web python manage.py shell

# bajar el stack
docker compose -f docker-compose.local.yml down
```

No pases `docker compose up` sin `-f`: eso carga `docker-compose.override.yml` (perfil de producción, pide `.env` con secretos y certificados de nginx).

### Correr Django local, sin Docker

Solo la base de datos va en Docker. Django corre nativo, con venv.

```bash
docker compose -f docker-compose.local.yml up -d db
export DJANGO_SETTINGS_MODULE=payment_system.settings.local
export DB_HOST=localhost
python manage.py migrate
python manage.py runserver
```

`local.py` usa Postgres, no SQLite. `DB_HOST=localhost` porque el container mapea el puerto 5432 a tu máquina, no a `db` (ese nombre solo resuelve dentro de la red de Docker). Sin Redis: `CELERY_TASK_ALWAYS_EAGER=True` corre las tasks sin broker.

### Verificar antes de commitear

`make verify` necesita Postgres arriba. Si no tenés el stack completo corriendo, alcanza con la base:

```bash
docker compose -f docker-compose.local.yml up -d db
pip install -r requirements-dev.txt   # una sola vez
make verify
```

`make verify` corre lint, `manage.py check`, el chequeo de migraciones faltantes y los 230 tests. Es el contrato: si da verde, el cambio se puede mergear.

| Objetivo | Cuándo |
|---|---|
| `make verify` | Antes de abrir un Pull Request |
| `make format` | Cuando `make lint` se queja de formato |
| `make test` | Mientras iterás, para saltear el lint |

Usa `python3`. Si tu intérprete es otro: `make verify PYTHON=venv/bin/python`.

### Deploy

Producción y staging corren con Docker Compose (`docker-compose.yml` + `docker-compose.override.yml`, y `docker-compose.staging.yml`). Los dominios de `nginx/` y `payment_system/settings/` son de ejemplo (`example.com`): cambialos por los tuyos.

```bash
docker compose up -d --build
docker compose exec web python manage.py migrate --noinput
```

### Licencia

MIT. Ver [LICENSE](LICENSE).
