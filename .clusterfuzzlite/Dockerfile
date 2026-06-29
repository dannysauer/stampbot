FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:ef0903c727eede93baa266d3ba12bb5f0329596201bad7bcd68de47b7cf9574d

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
