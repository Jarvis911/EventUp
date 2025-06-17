import cloudinary.api
import os
import firebase_admin
from firebase_admin import credentials

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
MEDIA_URL = '/media/'

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_ROOT = os.path.join(BASE_DIR, 'static')
STATIC_URL = '/static/'

cred_path = os.path.join(BASE_DIR, 'event_up', 'eventup-98bab-firebase-adminsdk-fbsvc-bceb574688.json')
cred = credentials.Certificate(cred_path)

firebase_admin.initialize_app(cred)

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-(*r_4w(vgrj=4pkz@66$*f)5mr+wk64i^pn=hc+q1hm9yc34g!'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']

CSRF_TRUSTED_ORIGINS = [
    'https://7476-115-73-217-251.ngrok-free.app',
]

# Application definition

INSTALLED_APPS = [
    'corsheaders',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'event_up_v1',
    'ckeditor',
    'ckeditor_uploader',
    'drf_yasg',
    'oauth2_provider',
    'debug_toolbar',
    'django_filters',
    'django_celery_beat',
    'celery',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'oauth2_provider.contrib.rest_framework.OAuth2Authentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.AllowAny',
    ),
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
    ],
    'PAGE_SIZE': 20
}

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}

CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'

CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers.DatabaseScheduler'


CKEDITOR_UPLOAD_PATH = "ckeditors/events/"  # Thư mục lưu ảnh và file tải lên

CKEDITOR_CONFIGS = {
    'default': {
        'toolbar': 'full',
        'height': 300,
        'width': '100%',
        'extraPlugins': ','.join(['image2']),  # Thêm plugin image2 để quản lý ảnh
    },
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'oauth2_provider.middleware.OAuth2TokenMiddleware',
    'debug_toolbar.middleware.DebugToolbarMiddleware',
]

CORS_ALLOW_ALL_ORIGINS = True

ROOT_URLCONF = 'event_up.urls'

INTERNAL_IPS = [
    '127.0.0.1'
]

# Configuration
cloudinary.config(
    cloud_name="dpfbtypxx",
    api_key="191724613981669",
    api_secret="4fgFs1HJMoFsbFnaG61IMCW10IM",  # Click 'View API Keys' above to copy your API secret
    secure=True
)

DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

AUTH_USER_MODEL = 'event_up_v1.User'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'event_up.wsgi.application'

# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'eventdb',
        'USER': 'root',
        'PASSWORD': '2004@Conmadoi',
        'HOST': ''
    }
}

# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Asia/Ho_Chi_Minh'

USE_I18N = True

USE_TZ = True

DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10MB

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication with OAuth2 and User
AUTHENTICATION_BACKENDS = (
    'oauth2_provider.backends.OAuth2Backend',
    'django.contrib.auth.backends.ModelBackend',
)

OAUTH2_PROVIDER = {
    # 'OAUTH2_BACKEND_CLASS': 'oauth2_provider.oauth2_backends.JSONOAuthLibCore',
    'SCOPES': {
        'read': 'Read scope',
        'write': 'Write scope',
    },
    'DEFAULT_SCOPES': ['read', 'write'],
}

CLIENT_ID = 'AZzHCDaw5vMIWUW7f0vuqhVunNNvwe8HhPdpxxBE'
CLIENT_SECRET = 'pcWoNqX3tQnZsAzPr3ZF4Z1E3WIydx5v5WRwBzxQbQaIFHdMmL29Vkkjd7rg6u926EVPte8rMkHxIrlDmNyfXuS7E6Tb2XkAz6M2RF4yUUug3HXg8IgRfLYJ4Cw0v4yg'

# Sending notifications through email config
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'triho753@gmail.com'
EMAIL_HOST_PASSWORD = 'kxod ytzu yzmp xfno'
DEFAULT_FROM_EMAIL = 'Event Up <triho753@gmail.com>'

ALLOWED_REDIRECT_SCHEMES = ['http', 'https', 'eventup']
# MoMo Sandbox configuration
MOMO_PARTNER_CODE = "MOMO"
MOMO_ACCESS_KEY = "F8BBA842ECF85"
MOMO_SECRET_KEY = "K951B6PE1waDMi640xX08PD3vg6EkVlz"
MOMO_ENDPOINT = "https://test-payment.momo.vn/v2/gateway/api/create"
MOMO_IPN_URL = "https://7476-115-73-217-251.ngrok-free.app/invoice/momo/ipn/"
MOMO_REDIRECT_URL = "eventup://payment-result"

