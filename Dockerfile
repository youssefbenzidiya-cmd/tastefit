FROM node:22-alpine
RUN apk upgrade --no-cache && apk add --no-cache python3
WORKDIR /app
COPY package.json .
RUN npm install --omit=dev && npm cache clean --force
COPY . .
ENV PORT=7860 QLOO_BASE_URL=https://hackathon.api.qloo.com HOME=/tmp
EXPOSE 7860
USER node
CMD ["python3", "app.py"]
