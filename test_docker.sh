echo 'ARG BUILD_ARCH=amd64' > Dockerfile.test
echo 'FROM ghcr.io/home-assistant/${BUILD_ARCH}-base-python:3.12-alpine3.20' >> Dockerfile.test
echo 'RUN echo "Success!"' >> Dockerfile.test
docker build --build-arg BUILD_ARCH=aarch64 -f Dockerfile.test .
