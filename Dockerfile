# Lightweight runtime image. Training and MLflow dependencies stay outside it.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY knowledge ./knowledge
RUN addgroup --system opspilot && adduser --system --ingroup opspilot opspilot \
    && chown -R opspilot:opspilot /app
USER opspilot
EXPOSE 8080
CMD ["python", "-m", "app.run"]
