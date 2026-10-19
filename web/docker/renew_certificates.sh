#!/bin/bash

# certbot's standalone server needs port 80, so only the web stops; the API keeps running.
# The repository's .env selects the compose files (deploy/*.env).
cd /home/ubuntu/moleonline || exit 1

docker compose stop web

certbot renew

docker compose start web
