import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8080")
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "gthread"
threads = int(os.environ.get("WEB_THREADS", "4"))
timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 2000
max_requests_jitter = 200
accesslog = "-"
errorlog = "-"
# Evitar persistir query strings que pueden contener datos ciudadanos.
access_log_format = "%(h)s %(m)s %(U)s %(s)s %(L)s"
preload_app = False


def on_starting(server):
    # Migraciones una vez antes de crear los workers; no compartir pools tras fork.
    import server as application

    application.init()
    if application._pool is not None:
        application._pool.close()
        application._pool = None
