FROM node:24-slim AS build

WORKDIR /build

COPY package*.json ./

RUN npm ci

COPY . ./

# Path of the MOLE API baked into the site, served by this site's nginx (site.conf)
ARG VITE_API_URL=/api

RUN npm run build

FROM nginx:1.30-alpine-slim

COPY --from=build /build/dist/ /var/www
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY docker/site.conf /etc/nginx/site.conf
COPY docker/conf.d/ /etc/nginx/templates/

# nginx runs as the user nginx: the entrypoint writes the templates to conf.d, filling in the variables
# below and the container's DNS servers; nginx keeps its temp files in /var/cache/nginx; the stock
# server on port 80 is not used
RUN rm /etc/nginx/conf.d/default.conf && chown nginx /etc/nginx/conf.d /var/cache/nginx
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
# where nginx forwards /api/ (conf.d/api.conf.template): compose's service; in Kubernetes the API's
# service by its full name, which nginx's resolver needs (it does not search the cluster's domains)
ENV API_ADDRESS=api:8080
# conf.d/clients.conf.template: the proxy in front of nginx whose X-Forwarded-For is trusted (none by default), the
# requests to the API one client may make per second on average
ENV TRUSTED_PROXY=127.0.0.1/32 API_REQUESTS_PER_SECOND=50

USER nginx

CMD ["nginx", "-g", "daemon off;"]
