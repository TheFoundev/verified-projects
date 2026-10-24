FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

EXPOSE 8000

# Default to dev server - gunicorn can be added if needed
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
