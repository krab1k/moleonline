# MOLEonline API with MOLE 2, the pores tool (tools/pores) and MEMEMBED.
# Computations, the submission log and downloaded OPM files are kept in /data.

ARG DOTNET_VERSION=10.0

# MOLE 2, framework-dependent, it runs on the .NET runtime of the final image
FROM docker.io/sbncbr/mole2:2.5.26.10.17 AS mole2

# MEMEMBED (GPL v2): https://github.com/psipred/MemEmbed, unmodified, built without -Werror (newer compilers warn)
FROM mcr.microsoft.com/dotnet/aspnet:${DOTNET_VERSION} AS memembed
ARG MEMEMBED_COMMIT=d18b9f55604d27e0429ae871fa252f13ce485fa4
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git g++ make libboost-thread-dev libboost-system-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone https://github.com/psipred/MemEmbed.git . \
    && git checkout ${MEMEMBED_COMMIT} \
    && make CFLAGS="-Wall -O3 -fPIC -std=c++11" \
        LIBS="-Wl,-Bstatic -lboost_thread -lboost_system -Wl,-Bdynamic -pthread" \
    && mkdir -p /opt/memembed/source \
    && cp bin/memembed LICENSE /usr/share/common-licenses/GPL-2 /opt/memembed/ \
    && cp -r Makefile README src /opt/memembed/source/ && rm /opt/memembed/source/src/*.o \
    && printf 'MEMEMBED, https://github.com/psipred/MemEmbed, commit %s, unmodified (source/).\nBuilt with: make CFLAGS="-Wall -O3 -fPIC -std=c++11", Boost linked statically.\nCopyright (C) Tim Nugent 2013, GNU General Public License v2 or later (LICENSE, GPL-2).\n' ${MEMEMBED_COMMIT} > /opt/memembed/SOURCE

# the API and the pores tool, each in its own environment of one Python, installed by uv
FROM mcr.microsoft.com/dotnet/aspnet:${DOTNET_VERSION} AS python
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON_PREFERENCE=only-managed UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /src/pores
COPY tools/pores/pyproject.toml tools/pores/uv.lock tools/pores/.python-version ./
RUN UV_PROJECT_ENVIRONMENT=/opt/pores uv sync --frozen --no-dev --no-install-project
COPY tools/pores/src ./src
RUN UV_PROJECT_ENVIRONMENT=/opt/pores uv sync --frozen --no-dev --no-editable
WORKDIR /src/api
COPY pyproject.toml uv.lock .python-version ./
RUN UV_PROJECT_ENVIRONMENT=/opt/api uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN UV_PROJECT_ENVIRONMENT=/opt/api uv sync --frozen --no-dev --no-editable

# the .NET runtime for MOLE
FROM mcr.microsoft.com/dotnet/aspnet:${DOTNET_VERSION}
COPY --from=mole2 /app /opt/mole2
COPY --from=memembed /opt/memembed /opt/memembed
COPY --from=python /opt/python /opt/python
COPY --from=python /opt/pores /opt/pores
COPY --from=python /opt/api /opt/api
RUN mkdir /data && chown $APP_UID:$APP_UID /data
ENV MEMEMBED=/opt/memembed/memembed PORES_OPM_CACHE=/data/opm-cache
WORKDIR /app
USER $APP_UID
EXPOSE 8080
# one process: the runs and their slots are kept in memory; requests are logged by nginx
ENTRYPOINT ["/opt/api/bin/uvicorn", "mole_api.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
