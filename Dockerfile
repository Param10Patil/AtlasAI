# Lightweight runtime image. Training and MLflow dependencies stay outside it.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN addgroup --system atlasai && adduser --system --ingroup atlasai atlasai
COPY --chown=atlasai:atlasai app ./app
COPY --chown=atlasai:atlasai static ./static
COPY --chown=atlasai:atlasai knowledge ./knowledge
USER atlasai
EXPOSE 8080
CMD ["python", "-m", "app.run"]
