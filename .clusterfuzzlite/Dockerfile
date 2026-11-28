FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:ed78b82bdc5a9fde750bc83f50594042a8918f2ddb5a2f6e7b1a4b8ad79c893c

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
