ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${NODE_IMAGE} AS studio
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG CODEX_VERSION=0.169.0
WORKDIR /build
COPY Harness/studio/package.json Harness/studio/package-lock.json ./
RUN npm ci
RUN npm install --prefix /codex --include=optional @openai/codex@${CODEX_VERSION}
COPY Harness/studio/ ./
RUN npm run build
RUN mkdir -p /third-party/studio \
    && cd /build/node_modules \
    && find . -type f \( -iname 'license*' -o -iname 'copying*' -o -iname 'notice*' -o -iname 'copyright*' \) \
       -exec cp --parents '{}' /third-party/studio/ \;

FROM ${PYTHON_IMAGE}
ARG HTTP_PROXY
ARG HTTPS_PROXY
LABEL org.opencontainers.image.title="PrintLab" \
    org.opencontainers.image.source="https://github.com/s450586793/printlab" \
    org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    PRINTLAB_SOURCE_ROOT=/opt/printlab PRINTLAB_ROOT=/data \
    PRINTLAB_HOST=0.0.0.0 PRINTLAB_PORT=8018 PRINTLAB_STUDIO_PORT=8018 \
    PRINTLAB_CAD_PYTHON=/opt/venv/bin/python \
    PRINTLAB_BLENDER_PATH=/usr/local/bin/printlab-blender \
    PRINTLAB_SLICER_PATH=/usr/local/bin/printlab-slicer \
    PRINTLAB_SLICER_PROFILES=/opt/bambu-studio/resources/profiles \
    CODEX_HOME=/data/Runtime/codex \
    PRINTLAB_CODEX_BINARY=/usr/local/bin/codex \
    LIBGL_ALWAYS_SOFTWARE=1 \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
    blender xvfb xauth libgl1 libglu1-mesa libglib2.0-0 libxrender1 \
    libxkbcommon0 libxext6 libsm6 libice6 libgtk-3-0 libwebkit2gtk-4.1-0 \
    git ripgrep curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/install-bambu-studio /tmp/install-bambu-studio
COPY deploy/printlab-slicer /usr/local/bin/printlab-slicer
RUN chmod 755 /tmp/install-bambu-studio /usr/local/bin/printlab-slicer \
    && /tmp/install-bambu-studio \
    && rm /tmp/install-bambu-studio
COPY deploy/bambu-studio.LICENSE /opt/bambu-studio/LICENSE
COPY --from=studio /usr/local/bin/node /usr/local/bin/node
COPY --from=studio /usr/local/LICENSE /usr/share/doc/printlab/third-party/node/LICENSE
COPY --from=studio /codex/node_modules /opt/codex/node_modules
COPY --from=studio /third-party/studio /usr/share/doc/printlab/third-party/studio
COPY --from=studio /build/package-lock.json /usr/share/doc/printlab/studio-package-lock.json
RUN ln -s /opt/codex/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && codex --version
WORKDIR /opt/printlab
RUN apt-get update && apt-get install -y --no-install-recommends python3-numpy \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/cad-requirements.txt /tmp/printlab-cad-requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/printlab-cad-requirements.txt \
    && /usr/sbin/useradd --create-home --uid 1000 printlab \
    && mkdir /data && chown printlab:printlab /data
COPY deploy/printlab-blender /usr/local/bin/printlab-blender
RUN chmod 755 /usr/local/bin/printlab-blender
COPY . .
COPY --from=studio /build/dist ./Harness/studio/dist
RUN chmod -R a+rX /opt/printlab \
    && /opt/venv/bin/pip install --no-cache-dir ./Harness \
    && ln -s /opt/venv/bin/printlab /usr/local/bin/printlab
RUN cp LICENSE.md NOTICE.txt THIRD_PARTY_NOTICES.md /usr/share/doc/printlab/ \
    && /opt/venv/bin/python -m pip list --format=json > /usr/share/doc/printlab/python-packages.json \
    && dpkg-query -W -f='${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n' > /usr/share/doc/printlab/debian-packages.tsv \
    && node --version > /usr/share/doc/printlab/node-version.txt \
    && codex --version > /usr/share/doc/printlab/codex-version.txt \
    && python --version > /usr/share/doc/printlab/python-version.txt
USER printlab
RUN /usr/bin/timeout --kill-after=5s 30s /usr/local/bin/printlab-slicer --help > /tmp/printlab-slicer-help.txt \
    && rg --quiet '^BambuStudio-02[.]09[.]01[.]70:$' /tmp/printlab-slicer-help.txt \
    && for option in --load-settings --load-filaments --slice --export-3mf; do \
        rg --quiet --fixed-strings -- "$option" /tmp/printlab-slicer-help.txt || exit 1; \
    done \
    && rm /tmp/printlab-slicer-help.txt
VOLUME /data
EXPOSE 8018
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s \
    CMD ["/opt/venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8018/api/health', timeout=3)"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/venv/bin/python", "-m", "printlab.container_runtime"]
