FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:8476df732bcde364e001aa74b5df836ca28ff9c6acefb170cd9fb34ab07feea7

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
