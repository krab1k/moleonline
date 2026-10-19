#!/bin/bash

# certbot's standalone server needs port 80, so only the web stops; the API keeps running
COMPOSE="docker compose -f /home/ubuntu/moleonline/compose.yaml -f /home/ubuntu/moleonline/compose.production.yaml"

$COMPOSE stop web

certbot renew

$COMPOSE start web
