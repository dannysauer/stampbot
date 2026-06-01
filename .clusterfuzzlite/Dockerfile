FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:e4115b784c4ca914cd88e17d033c653c894e2a05b6e034dd93d9a0c4a309df1e

COPY . $SRC/stampbot-project
WORKDIR $SRC/stampbot-project
COPY .clusterfuzzlite/build.sh $SRC/build.sh
