# Ponto Zero — imagem de produção.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Navegador do Playwright + dependências de SO (para o conector de site real).
# --with-deps instala as libs do sistema que o Chromium headless precisa.
RUN playwright install --with-deps chromium

COPY . .

# O Render/host injeta $PORT; o server.py faz bind em 0.0.0.0:$PORT.
ENV PORT=8000
EXPOSE 8000

CMD ["python", "server.py"]
