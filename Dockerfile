# Ponto Zero — imagem de produção (v1 roda em stdlib pura).
FROM python:3.12-slim

WORKDIR /app

# Em v1 não há dependências externas (stdlib). Mantemos o passo para evoluir
# (ex.: Playwright/FastAPI) sem mudar o pipeline de build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# O Render/host injeta $PORT; o server.py faz bind em 0.0.0.0:$PORT.
ENV PORT=8000
EXPOSE 8000

CMD ["python", "server.py"]
