FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:d74383a516f17ae3300aee7160277adf0702a22ca400d2dc54cd79f4b4c86b70

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
