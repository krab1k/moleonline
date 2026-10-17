# MOLEonline API (src/mole_api) with MOLE 2, the pores tool (tools/pores) and MEMEMBED; MOLE needs the .NET runtime.
# Computations, the submission log and downloaded OPM files are kept in /data.

# MEMEMBED (GPL v2): https://github.com/psipred/MemEmbed, unmodified, built without -Werror (newer compilers warn);
# its licence and source are in /opt/memembed
FROM mcr.microsoft.com/dotnet/runtime:10.0 AS memembed
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git g++ make libboost-thread-dev libboost-system-dev
WORKDIR /src
RUN git clone https://github.com/psipred/MemEmbed.git . && git checkout d18b9f55604d27e0429ae871fa252f13ce485fa4 \
    && make CFLAGS="-Wall -O3 -fPIC -std=c++11" LIBS="-Wl,-Bstatic -lboost_thread -lboost_system -Wl,-Bdynamic -pthread" \
    && mkdir /opt/memembed && cp bin/memembed LICENSE /usr/share/common-licenses/GPL-2 /opt/memembed/ \
    && git archive -o /opt/memembed/source.tar.gz HEAD

# the API and the pores tool in one environment, with Python, installed by uv
FROM mcr.microsoft.com/dotnet/runtime:10.0 AS api
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY src src
COPY tools/pores tools/pores
RUN uv sync --frozen --no-dev --no-editable

FROM mcr.microsoft.com/dotnet/runtime:10.0
COPY --from=docker.io/sbncbr/mole2:2.5.26.10.17 /app /opt/mole2
COPY --from=memembed /opt/memembed /opt/memembed
COPY --from=api /opt/python /opt/python
COPY --from=api /app/.venv /app/.venv
RUN mkdir /data && chown $APP_UID:$APP_UID /data
ENV MEMEMBED=/opt/memembed/memembed PORES_OPM_CACHE=/data/opm-cache
USER $APP_UID
EXPOSE 8080
# one process: the runs and their slots are kept in memory; requests are logged by nginx
ENTRYPOINT ["/app/.venv/bin/uvicorn", "mole_api.main:app", "--host", "0.0.0.0", "--port", "8080", "--root-path", "/api", "--no-access-log"]
