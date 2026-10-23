# Caddy for docker-compose.prod.yml (the Oracle-VM deploy path).
#
# Built, not pulled, for the same reason as the root Dockerfile's stage 2b: the
# prebuilt caddy:2.11.7-alpine binary was compiled with Go 1.26.8 and carries
# CVE-2026-111943 (crypto/x509, HIGH, fixed in Go 1.26.10). The builder image is
# refreshed with Go security releases far more often than the release image, so
# compiling the SAME pinned Caddy release with it gives this path the patched Go
# stdlib too. Only the binary is replaced; the runtime image and its Caddyfile,
# /data and /config layout are the official ones.
#
# Keep CADDY_VERSION and both tags equal to the root Dockerfile's stage 2b, and
# bump all of them together. Never build an untagged tree.
FROM caddy:2.11.7-builder-alpine AS build
ARG CADDY_VERSION=v2.11.7
ENV CGO_ENABLED=0
RUN xcaddy build "${CADDY_VERSION}" --output /usr/bin/caddy \
    && go version \
    && /usr/bin/caddy version

FROM caddy:2.11.7-alpine
COPY --from=build /usr/bin/caddy /usr/bin/caddy
