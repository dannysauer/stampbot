FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:b94ef3a542cce309dd9ec739b277d7057a6c6f91392137260d9731a4e0703e67

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
