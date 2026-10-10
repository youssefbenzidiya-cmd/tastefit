FROM node:22-trixie-slim
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json .
RUN npm install --omit=dev
COPY . .
ENV PORT=7860 QLOO_BASE_URL=https://hackathon.api.qloo.com HOME=/tmp
EXPOSE 7860
USER node
CMD ["python3", "app.py"]
