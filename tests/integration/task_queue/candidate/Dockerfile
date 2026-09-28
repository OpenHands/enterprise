FROM tq-poc-base
# Supercronic v0.2.49; SHA1 sums are the ones published in the release notes.
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.49
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH} /usr/local/bin/supercronic
RUN case "${TARGETARCH}" in \
      amd64) sum=e63c11a9726b775a6a11801e81af4f3fb926aa68 ;; \
      arm64) sum=0b6c5bb743e0b0dafed1132198c81807927ac413 ;; \
      *) echo "no pinned checksum for ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && echo "${sum}  /usr/local/bin/supercronic" | sha1sum -c - \
    && chmod +x /usr/local/bin/supercronic
COPY . /app
