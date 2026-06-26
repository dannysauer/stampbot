FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:e0e18dea5d2e4107a4f5d04652e6c278fbea32dc6d6861ee8de29402d0916746

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
