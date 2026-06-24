FROM node:20-slim

WORKDIR /app

# Prisma's query/schema engines need OpenSSL (libssl) at runtime. Debian "slim"
# images don't ship it by default, and Alpine/musl trips Prisma's engine loader
# (the "failed to detect libssl" crash) — so install it explicitly here.
RUN apt-get update -y \
 && apt-get install -y --no-install-recommends openssl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install deps first (layer cached unless package.json changes)
COPY package.json ./
RUN npm install

# Generate the Prisma client (needs the schema)
COPY prisma ./prisma
RUN npx prisma generate

# App source + TS config. We run TS directly via tsx (no build step in M1).
COPY tsconfig.json ./
COPY src ./src

EXPOSE 3000
CMD ["npm", "start"]
