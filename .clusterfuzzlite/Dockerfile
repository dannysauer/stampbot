FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:8d8b99b52e763c74b5b221a9ad5cabdac1998c26c206b7020786e6019859c985

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
