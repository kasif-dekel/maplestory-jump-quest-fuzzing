# Headless build of the MapleStory client for fuzzing.
#
# The real Stage/Player/Physics/Portal code is compiled unchanged; rendering,
# audio, windowing and the socket layer are replaced by no-op stubs. Build with
# afl-clang-fast++ and -DIJON_DIR=<ijon checkout> for the instrumented target.
cmake_minimum_required(VERSION 3.10)
project(ZakumFuzz C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

set(CLIENT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../src/client")
set(NLNX_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../src/nlnx")
set(IJON_DIR "" CACHE PATH "Path to the IJON (AFL fork) checkout; enables IJON annotations")

file(GLOB_RECURSE CLIENT_SOURCES
    "${CLIENT_DIR}/Character/*.cpp"
    "${CLIENT_DIR}/Data/*.cpp"
    "${CLIENT_DIR}/Gameplay/*.cpp"
    "${CLIENT_DIR}/Graphics/*.cpp"
    "${CLIENT_DIR}/IO/*.cpp"
    "${CLIENT_DIR}/Net/*.cpp"
    "${CLIENT_DIR}/Offline/*.cpp"
    "${CLIENT_DIR}/Template/*.cpp"
    "${CLIENT_DIR}/Util/*.cpp"
)
# Platform layers replaced by fuzz/stubs.
list(FILTER CLIENT_SOURCES EXCLUDE REGEX "Graphics/GraphicsGL\\.cpp$")
list(FILTER CLIENT_SOURCES EXCLUDE REGEX "IO/Window\\.cpp$")
list(FILTER CLIENT_SOURCES EXCLUDE REGEX "Net/Session\\.cpp$")
list(FILTER CLIENT_SOURCES EXCLUDE REGEX "Net/SockInternal\\.cpp$")
list(FILTER CLIENT_SOURCES EXCLUDE REGEX "Net/SocketAsio\\.cpp$")
list(FILTER CLIENT_SOURCES EXCLUDE REGEX "Net/SocketWinsock\\.cpp$")

file(GLOB STUB_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/stubs/*.cpp")

add_executable(zakum_harness
    harness.cpp
    ${STUB_SOURCES}
    ${CLIENT_SOURCES}
    "${CLIENT_DIR}/Configuration.cpp"
    "${CLIENT_DIR}/includes/lz4.c"
    "${NLNX_DIR}/bitmap.cpp"
    "${NLNX_DIR}/node.cpp"
    "${NLNX_DIR}/audio.cpp"
    "${NLNX_DIR}/file.cpp"
    "${NLNX_DIR}/nx.cpp"
)

target_compile_definitions(zakum_harness PRIVATE MS_HEADLESS=1)
# Stub headers must shadow the vendored GL shim and any system GLFW/FreeType.
target_include_directories(zakum_harness BEFORE PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/stubs/include")
target_include_directories(zakum_harness PRIVATE
    "${CLIENT_DIR}"
    "${CLIENT_DIR}/.."
    "${CLIENT_DIR}/includes"
    "${CLIENT_DIR}/includes/stb"
)
# Emscripten pulls <stddef.h>/<stdint.h> in implicitly; several client headers
# rely on that, so force-include them here instead of patching each header.
target_compile_options(zakum_harness PRIVATE
    "SHELL:-include stdint.h" "SHELL:-include stddef.h"
    -Wall -Wno-narrowing -Wno-sign-compare -Wno-unused-parameter)
find_package(Threads REQUIRED)
target_link_libraries(zakum_harness PRIVATE Threads::Threads)

if(IJON_DIR)
    target_compile_definitions(zakum_harness PRIVATE IJON_ENABLED=1)
    target_include_directories(zakum_harness PRIVATE "${IJON_DIR}/llvm_mode")
endif()
