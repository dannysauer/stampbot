FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:04d1a93ee11e221f464dde307b3003c6a5f4a9ac148f00ceff3006e777467107

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
