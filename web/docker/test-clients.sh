#!/bin/sh
# The client nginx tells the API and its rate limit (conf.d/clients.conf.template), with the website's image in front of
# an echo server that answers with the X-Forwarded-For it gets, as the API would see it, and the path in X-Path.
# Usage: test-clients.sh <the website's image>; DOCKER=podman for podman
set -eu
D=${DOCKER:-docker}
IMAGE=$1
N=moleonline-clients-test

cleanup() {
    $D rm -f "$N-echo" "$N-web" "$N-proxied" >/dev/null 2>&1 || true
    $D network rm "$N" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup
$D network create "$N" >/dev/null

$D run -d --name "$N-echo" --network "$N" --network-alias echo docker.io/library/python:3.14-alpine python -c '
import http.server

class Echo(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = (self.headers.get("X-Forwarded-For") or "").encode()
        self.send_response(200)
        self.send_header("X-Path", self.path)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments):
        pass

http.server.ThreadingHTTPServer(("", 8080), Echo).serve_forever()' >/dev/null

# web: as in compose, no proxy in front; proxied: behind a proxy it trusts, with a low rate for the flood
$D run -d --name "$N-web" --network "$N" -p 127.0.0.1::8080 -e API_ADDRESS=echo:8080 "$IMAGE" >/dev/null
$D run -d --name "$N-proxied" --network "$N" -p 127.0.0.1::8080 -e API_ADDRESS=echo:8080 \
    -e TRUSTED_PROXY=0.0.0.0/0 -e API_REQUESTS_PER_SECOND=1 "$IMAGE" >/dev/null
WEB=http://$($D port "$N-web" 8080 | head -n 1)
PROXIED=http://$($D port "$N-proxied" 8080 | head -n 1)

for url in "$WEB" "$PROXIED"; do
    tries=0
    until curl -sf -o /dev/null "$url/api/ready"; do
        tries=$((tries + 1))
        [ "$tries" -lt 50 ] || { echo "nginx at $url did not answer"; exit 1; }
        sleep 0.2
    done
done

fail() { echo "FAILED: $1"; exit 1; }

seen=$(curl -s -H "X-Forwarded-For: 203.0.113.9" "$WEB/api/x")
case "$seen" in
    *203.0.113.9* | *,* | "") fail "without a trusted proxy the API got '$seen', not only who connected" ;;
esac
echo "ok: a client's own X-Forwarded-For is ignored ($seen)"

seen=$(curl -s -H "X-Forwarded-For: 198.51.100.1, 203.0.113.5" "$PROXIED/api/x")
[ "$seen" = "203.0.113.5" ] || fail "behind a trusted proxy the API got '$seen', not the address the proxy added (203.0.113.5)"
echo "ok: behind a trusted proxy, the address it added"

answer=$(curl -s -D - -H "X-Forwarded-For: 198.51.100.1, 203.0.113.5" "$PROXIED/mcp")
echo "$answer" | grep -qi "^x-path: /mcp" || fail "/mcp did not reach the API's /mcp: $answer"
[ "$(echo "$answer" | tail -n 1)" = "203.0.113.5" ] || fail "/mcp did not tell the API the client: $answer"
echo "ok: /mcp reaches the API's MCP server, with the client"

codes=$(seq 300 | xargs -P 30 -I{} curl -s -o /dev/null -w "%{http_code}\n" "$PROXIED/api/x" | sort | uniq -c)
echo "$codes" | grep -q " 429$" || fail "300 requests at once were not limited: $codes"
for _ in $(seq 10); do  # the rate lets a request through each second
    problem=$(curl -s -D - "$PROXIED/api/x")
    case "$problem" in HTTP/1.1\ 429*) break ;; esac
done
echo "$problem" | grep -qi "^retry-after: 1" || fail "the 429 has no Retry-After: $problem"
echo "$problem" | grep -q '"detail":"Too many requests from your address. Please, slow down."' || fail "the 429 is not problem details: $problem"
echo "ok: a flood is limited with 429 ($(echo "$codes" | tr -s ' ' | tr '\n' ' '))"

