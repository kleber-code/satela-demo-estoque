"""Configuração do projeto. Tudo que varia por ambiente vem de variáveis de ambiente."""
import os
import secrets
import sys
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path("/data")
TEM_DATA = DATA_DIR.is_dir()


def env_bool(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None or valor == "":
        return padrao
    return valor.strip().lower() in ("1", "true", "t", "yes", "sim", "on")


def env_lista(nome, padrao=""):
    return [v.strip() for v in os.environ.get(nome, padrao).split(",") if v.strip()]


DEBUG = env_bool("DEBUG", False)
TESTANDO = "test" in sys.argv


def _chave_secreta():
    chave = os.environ.get("DJANGO_SECRET_KEY") or os.environ.get("SECRET_KEY")
    if chave:
        return chave
    if DEBUG or TESTANDO:
        return "dev-inseguro-" + "x" * 50
    # Sem variável em produção: gera uma chave persistida em arquivo (fora do git), para que
    # todos os workers/reinícios usem a mesma. O ideal é definir DJANGO_SECRET_KEY.
    pasta = DATA_DIR if TEM_DATA else BASE_DIR
    arquivo = pasta / ".secret_key"
    try:
        fd = os.open(arquivo, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    except OSError:
        return secrets.token_urlsafe(64)
    for _ in range(20):
        try:
            conteudo = arquivo.read_text().strip()
            if conteudo:
                return conteudo
        except OSError:
            break
    return secrets.token_urlsafe(64)


SECRET_KEY = _chave_secreta()

ALLOWED_HOSTS = env_lista("DJANGO_ALLOWED_HOSTS", ".satela.com.br,localhost,127.0.0.1,[::1]")
if DEBUG and not os.environ.get("DJANGO_ALLOWED_HOSTS"):
    ALLOWED_HOSTS.append("testserver")


def _csrf_origens():
    origens = env_lista("CSRF_TRUSTED_ORIGINS")
    for host in ALLOWED_HOSTS:
        if host in ("*", "localhost", "127.0.0.1", "[::1]", "testserver"):
            continue
        host = "*" + host if host.startswith(".") else host
        origens.append("https://" + host)
    return list(dict.fromkeys(origens))


CSRF_TRUSTED_ORIGINS = _csrf_origens()

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "estoque",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "estoque.middleware.AcessoMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "estoque.context.global_ctx",
            ],
        },
    },
]

_sqlite_padrao = (DATA_DIR if TEM_DATA else BASE_DIR) / "db.sqlite3"
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{_sqlite_padrao}", conn_max_age=60, conn_health_checks=True
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

LOGIN_URL = "/entrar/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/entrar/"

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = (DATA_DIR / "media") if TEM_DATA else (BASE_DIR / "media")

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if (DEBUG or TESTANDO)
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
WHITENOISE_MANIFEST_STRICT = False

# Uploads e requisições
FOTO_MAX_BYTES = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 3 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 3000  # formulário de contagem tem um campo por produto

# Segurança
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_REDIRECT_EXEMPT = [r"^saude/$"]
SECURE_HSTS_SECONDS = 0 if DEBUG else int(os.environ.get("SECURE_HSTS_SECONDS", "3600"))

# Sem envio real de e-mail
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Aplicação
DEMO_MODE = env_bool("DEMO_MODE", True)
DEMO_ADMIN_PASSWORD = os.environ.get("DEMO_ADMIN_PASSWORD", "")
DIAS_SEM_GIRO = int(os.environ.get("DIAS_SEM_GIRO", "60"))
LOGIN_MAX_TENTATIVAS = int(os.environ.get("LOGIN_MAX_TENTATIVAS", "6"))
LOGIN_JANELA_SEGUNDOS = int(os.environ.get("LOGIN_JANELA_SEGUNDOS", "300"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.request": {"level": "ERROR"}},
}
