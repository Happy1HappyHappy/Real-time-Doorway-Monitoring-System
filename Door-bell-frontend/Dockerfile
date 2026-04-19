# Stage 1: Build React app
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine
RUN apk add --no-cache apache2-utils
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Create auth credentials (user: demo, password: doorbell2026)
RUN htpasswd -cb /etc/nginx/.htpasswd demo doorbell2026

EXPOSE 80
