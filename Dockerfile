# Stage 1: Build frontend
FROM node:18-slim as frontend-builder

WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm ci

# FAST TURN 2（2026年9月）: Railway側のビルドキャッシュが、frontend/以下の
# ソース変更後もCOPY frontend ./ / RUN npm run buildの両レイヤーを誤って
# "cached"のまま再利用し、本番にfrontend/public配下の変更（realtime-voice-
# engine.js）が反映されない事象を確認したため、ARGでこのステージのキャッシュを
# 強制的に無効化する。値を変えるたびに以降のレイヤーがキャッシュされなくなる
# （Dockerの標準的なcache-bustパターン）。アプリの動作には一切影響しない。
ARG FRONTEND_CACHE_BUST=phase_o1_20260926
COPY frontend ./
RUN npm run build


# Stage 2: Runtime
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# FAST TURN 2（2026年9月）: 同じキャッシュ誤動作がこのステージのCOPY app ./app
# にも及んでいることをビルドログで確認したため（app/config.pyの変更が反映
# されない事象）、こちらも同様にARGで強制的にキャッシュを無効化する。
ARG BACKEND_CACHE_BUST=fastturn2_20260926
# Copy backend code
COPY app ./app

# Copy built frontend from stage 1
COPY --from=frontend-builder /frontend/dist ./frontend/dist

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
