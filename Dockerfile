FROM node:22-alpine
RUN apk upgrade --no-cache && apk add --no-cache python3
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY . .
ENV PORT=7860 QLOO_BASE_URL=https://hackathon.api.qloo.com HOME=/tmp
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/health" | grep -q '^ok' || exit 1
USER node
CMD ["python3", "app.py"]
