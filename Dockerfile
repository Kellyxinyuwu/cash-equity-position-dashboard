# Same book as the laptop: stages 2–4, no second P&L formula.
# The UI is built in the first stage. Python only serves it and reads SQLite.

FROM node:22-bookworm-slim AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY --from=ui /src/ui/dist ./ui/dist
RUN pip install --no-cache-dir .
ENV BOOK_DB=/data/book.sqlite
ENV BOOK_HOST=0.0.0.0
ENV BOOK_PORT=8000
EXPOSE 8000
# Keys stay outside the image. Mount the host data/ directory at /data
# so this process reads the same book.sqlite as the page on the laptop.
CMD ["python", "-m", "book_move", "serve"]
