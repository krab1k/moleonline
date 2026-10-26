# MOLEonline's backend (src/moleonline), two images of it:
#   --target api     the API: Python only
#   --target worker  a worker: with MOLE 2 (on the .NET runtime) and MEMEMBED for the pores tool; MOLE_VERSION picks
#                    MOLE's image, the worker reads the version from MOLE when it starts
# Both keep the sessions' files and downloaded OPM files in /data, as the same user (1654, the .NET image's app); the
# rest is in the database (DATABASE_URL).

ARG MOLE_VERSION=2.5.26.10.17
FROM docker.io/sbncbr/mole2:${MOLE_VERSION} AS mole

# MEMEMBED (GPL v2): https://github.com/psipred/MemEmbed, unmodified, built without -Werror (newer compilers warn);
# its licence and source are in /opt/memembed
FROM mcr.microsoft.com/dotnet/runtime:10.0 AS memembed
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git g++ make libboost-thread-dev libboost-system-dev
WORKDIR /src
RUN git clone https://github.com/psipred/MemEmbed.git . && git checkout d18b9f55604d27e0429ae871fa252f13ce485fa4 \
    && make CFLAGS="-Wall -O3 -fPIC -std=c++11" LIBS="-Wl,-Bstatic -lboost_thread -lboost_system -Wl,-Bdynamic -pthread" \
    && mkdir /opt/memembed && cp bin/memembed LICENSE /usr/share/common-licenses/GPL-2 /opt/memembed/ \
    && git archive -o /opt/memembed/source.tar.gz HEAD

# Python and the package, installed by uv: the API's dependencies, then also the workers'
FROM docker.io/library/ubuntu:26.04 AS python
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-default-groups --no-install-project  # cached while only the code changes
COPY src src
RUN uv sync --frozen --no-default-groups --no-editable

FROM python AS python-worker
RUN uv sync --frozen --no-default-groups --group worker --no-editable

FROM docker.io/library/ubuntu:26.04 AS api
RUN useradd --uid 1654 --user-group --no-create-home app && mkdir /data && chown app:app /data
COPY --from=python /opt/python /opt/python
COPY --from=python /app/.venv /app/.venv
USER 1654
EXPOSE 8080
# the reverse proxies whose X-Forwarded-For gives the client address (the request log), uvicorn's setting: private
# networks, where a proxy in front (nginx in compose, an ingress in Kubernetes) has an address not known in advance
ENV FORWARDED_ALLOW_IPS=127.0.0.0/8,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7
# requests are logged by the proxy in front. uvicorn reads its other options from UVICORN_* variables, e.g.
# UVICORN_ROOT_PATH=/api behind a proxy serving the API under /api/
ENTRYPOINT ["/app/.venv/bin/uvicorn", "moleonline.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM mcr.microsoft.com/dotnet/runtime:10.0 AS worker
COPY --from=mole /app /opt/mole2
COPY --from=memembed /opt/memembed /opt/memembed
COPY --from=python-worker /opt/python /opt/python
COPY --from=python-worker /app/.venv /app/.venv
RUN mkdir /data && chown $APP_UID:$APP_UID /data
USER $APP_UID
# SIGTERM (a deploy, maintenance) kills its run's processes; the run fails as interrupted
ENTRYPOINT ["/app/.venv/bin/python", "-m", "moleonline.worker"]
