web: gunicorn schoolsystem.wsgi:application --bind 0.0.0.0:$PORT
worker: celery -A schoolsystem worker --loglevel=INFO
beat: celery -A schoolsystem beat --loglevel=INFO
