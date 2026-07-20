FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:a5e01ec9a7fb6004fd6f5df7cdc5e5ca9d732a48f4b0e34b1d1fe91fe5c9a018

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
