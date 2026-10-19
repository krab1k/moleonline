#!/bin/bash
# certbot's deploy hook: after the certificate is issued or renewed, copy it where the site's nginx, which runs as
# the user nginx (101), can read it, and reload nginx. Linked as /etc/letsencrypt/renewal-hooks/deploy/moleonline.sh,
# certbot runs it as root with RENEWED_LINEAGE, the certificate's directory.
set -eu

CERTS=/var/lib/moleonline/certs
install -d -m 0700 -o 101 -g 101 "$CERTS"
install -m 0600 -o 101 -g 101 "$RENEWED_LINEAGE/fullchain.pem" "$RENEWED_LINEAGE/privkey.pem" "$CERTS/"

# the repository's root, whose .env selects the instance
cd "$(dirname "$(readlink -f "$0")")/.."
if [ -n "$(docker compose ps -q web)" ]; then
    docker compose exec -T web nginx -s reload
fi
