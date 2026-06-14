FROM node:20-alpine

WORKDIR /app

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
