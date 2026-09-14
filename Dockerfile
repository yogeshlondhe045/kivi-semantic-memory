# Node 22 is required: the database layer uses the built-in node:sqlite module.
FROM node:22-slim

WORKDIR /app
ENV NODE_ENV=development

COPY package.json package-lock.json* .npmrc ./
COPY packages/server/package.json packages/server/
COPY packages/web/package.json packages/web/
RUN npm install

COPY . .
RUN npm run build

ENV DATABASE_URL=/data/kivi.db
VOLUME /data
EXPOSE 8787

# Migrate and seed on first boot; subsequent boots find the data already there
# and start straight away.
CMD ["sh", "-c", "npm run db:migrate && npm run seed && npm start"]
