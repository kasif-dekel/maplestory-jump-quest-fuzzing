# Toolchain image for fuzzing the headless client with IJON (AFL fork).
#
# IJON's LLVM pass targets LLVM 6, so this stays on Ubuntu 18.04 / clang-6.0.
# Build context must be the ijon checkout:
#   docker build -t zakum-ijon -f fuzz/Dockerfile ../ijon
FROM ubuntu:18.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential clang-6.0 llvm-6.0 llvm-6.0-dev llvm-6.0-tools \
        make git python3 ca-certificates wget \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/clang-6.0 /usr/local/bin/clang \
    && ln -s /usr/bin/clang++-6.0 /usr/local/bin/clang++ \
    && ln -s /usr/bin/llvm-config-6.0 /usr/local/bin/llvm-config

# The fuzz CMakeLists uses "SHELL:" options (CMake >= 3.12); bionic ships 3.10.
ARG CMAKE_VERSION=3.27.9
RUN wget -qO- https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/cmake-${CMAKE_VERSION}-linux-x86_64.tar.gz \
        | tar xz -C /opt \
    && ln -s /opt/cmake-${CMAKE_VERSION}-linux-x86_64/bin/cmake /usr/local/bin/cmake

COPY . /opt/ijon
# Link the pass with -z nodelete: llvm_shutdown() in clang 6-8 dlclose()s
# plugins before destroying the pass-registration list that still points into
# them, which crashes cc1 at exit and fails every compile.
RUN cd /opt/ijon && make -j"$(nproc)" \
    && cd llvm_mode \
    && sed -i 's|^CLANG_LFL    = `$(LLVM_CONFIG) --ldflags` $(LDFLAGS)|CLANG_LFL    = `$(LLVM_CONFIG) --ldflags` -Wl,-z,nodelete $(LDFLAGS)|' Makefile \
    && grep -q "nodelete" Makefile \
    && LLVM_CONFIG=llvm-config-6.0 CC=clang-6.0 CXX=clang++-6.0 make

ENV AFL_PATH=/opt/ijon
ENV PATH=/opt/ijon:$PATH
WORKDIR /work
