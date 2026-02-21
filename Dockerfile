FROM postgres:15-alpine

ENV POSTGRES_USER=postgres
ENV POSTGRES_PASSWORD=postgres
EXPOSE 5432

# Basic healthcheck (requires `pg_isready` present in image)
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s \
	CMD pg_isready -U "$POSTGRES_USER" || exit 1

# Use the default entrypoint from the postgres image
