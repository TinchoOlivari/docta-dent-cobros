DJANGO_SETTINGS_MODULE ?= payment_system.settings.local
export DJANGO_SETTINGS_MODULE

# `python` suele ser un alias de shell y make no lo ve. Sobreescribilo así:
#   make test PYTHON=venv/bin/python
PYTHON ?= python3

# Tailwind CLI standalone (016, research P1). El binario NO se commitea: `make tools` lo baja.
TAILWIND_VERSION = 4.3.3
TAILWIND = bin/tailwindcss
CSS_IN = sistema/core/tailwind/input.css
CSS_OUT = sistema/core/static/sistema/css/app.css

.PHONY: lint format test check verify tools css css-watch css-check e2e

lint:
	ruff check .
	ruff format --check .
	$(PYTHON) -m djlint sistema --lint

format:
	ruff check --fix .
	ruff format .

test:
	$(PYTHON) manage.py test sistema --parallel --keepdb

check:
	$(PYTHON) manage.py check
	$(PYTHON) manage.py makemigrations --check --dry-run

# Baja el binario según uname -s / uname -m y verifica el sha256 (hashes de research P1).
tools:
	@set -e; \
	os=$$(uname -s); arch=$$(uname -m); \
	case "$$os" in Linux) os=linux;; Darwin) os=macos;; *) echo "Arquitectura no soportada: $$os"; exit 1;; esac; \
	case "$$arch" in x86_64) arch=x64;; aarch64|arm64) arch=arm64;; *) echo "Arquitectura no soportada: $$arch"; exit 1;; esac; \
	case "$$os-$$arch" in \
	  linux-x64) sha=dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a;; \
	  linux-arm64) sha=55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195;; \
	  macos-arm64) sha=cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d;; \
	  macos-x64) sha=7922e0953f2110c05976e3bf58f14e643d90427575e766b7d433f5f80cbee7e1;; \
	esac; \
	if [ -x $(TAILWIND) ] && [ "$$( (sha256sum $(TAILWIND) 2>/dev/null || shasum -a 256 $(TAILWIND)) | cut -d' ' -f1)" = "$$sha" ]; then exit 0; fi; \
	mkdir -p bin; \
	curl -fsSL -o $(TAILWIND) https://github.com/tailwindlabs/tailwindcss/releases/download/v$(TAILWIND_VERSION)/tailwindcss-$$os-$$arch; \
	got=$$( (sha256sum $(TAILWIND) 2>/dev/null || shasum -a 256 $(TAILWIND)) | cut -d' ' -f1); \
	if [ "$$got" != "$$sha" ]; then rm -f $(TAILWIND); echo "sha256 no coincide para tailwindcss-$$os-$$arch: $$got"; exit 1; fi; \
	chmod +x $(TAILWIND)

css: tools
	$(TAILWIND) -i $(CSS_IN) -o $(CSS_OUT) --minify

css-watch: tools
	$(TAILWIND) -i $(CSS_IN) -o $(CSS_OUT) --watch

# Compila a un temporal y compara con el app.css del repo.
css-check: tools
	@tmp=$$(mktemp); \
	$(TAILWIND) -i $(CSS_IN) -o $$tmp --minify >/dev/null 2>&1; \
	if cmp -s $$tmp $(CSS_OUT); then rm -f $$tmp; else rm -f $$tmp; echo "app.css desactualizado: corré make css"; exit 1; fi

# Pruebas con navegador (Playwright). Fuera de `make verify`. Una vez: playwright install chromium
# Usa su propia base de pruebas (`<DB_NAME>_e2e`, nueva en cada corrida): los flush de las pruebas con
# servidor real borrarían los datos sembrados de la base que `make test --keepdb` reutiliza.
e2e:
	DB_NAME=$${DB_NAME:-doctadent_local}_e2e DJANGO_ALLOW_ASYNC_UNSAFE=true $(PYTHON) manage.py test sistema.core.e2e --pattern "e2e_*.py" --noinput

verify: lint check css-check test
