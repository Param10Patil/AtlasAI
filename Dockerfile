# Lightweight runtime image. Training and MLflow dependencies stay outside it.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN addgroup --system opspilot && adduser --system --ingroup opspilot opspilot
COPY --chown=opspilot:opspilot app ./app
COPY --chown=opspilot:opspilot static ./static
COPY --chown=opspilot:opspilot knowledge ./knowledge
USER opspilot
EXPOSE 8080
CMD ["python", "-m", "app.run"]
