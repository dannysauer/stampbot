FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:3dbcb2fba5b92507146862ff7cd612908430137e60a112fd6425240163649a31

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
