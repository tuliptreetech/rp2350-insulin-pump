# Generated Cmake Pico project file

cmake_minimum_required(VERSION 3.13)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Initialise pico_sdk from installed location
# (note this can come from environment, CMake cache etc)

# == DO NOT EDIT THE FOLLOWING LINES for the Raspberry Pi Pico VS Code Extension to work ==
if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.3.1)
set(toolchainVersion RISCV_PICO_2_3_1_0)
set(picotoolVersion 2.3.1)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()
# ====================================================================================
set(PICO_BOARD pico2 CACHE STRING "Board type")

# Pull in Raspberry Pi Pico SDK (must be before project)
include(pico_sdk_import.cmake)

project(rp2350-insulin-pump C CXX ASM)

# Initialise the Raspberry Pi Pico SDK
pico_sdk_init()

# Add executable. Default name is the project name, version 0.1

add_executable(rp2350-insulin-pump
        src/main.c
        src/app/console.c
        src/app/pump.c
        src/app/safety.c
        src/app/ui.c
        src/drivers/abp_pressure.c
        src/drivers/drv8825.c
        src/drivers/font5x7.c
        src/drivers/slf3x.c
        src/drivers/ssd1306.c
        )

# The firmware itself is warning-clean under these; the SDK is not, so they
# are scoped to this target rather than set globally.
target_compile_options(rp2350-insulin-pump PRIVATE -Wall -Wextra)

pico_set_program_name(rp2350-insulin-pump "rp2350-insulin-pump")
pico_set_program_version(rp2350-insulin-pump "0.1")

# Modify the below lines to enable/disable output over UART/USB
pico_enable_stdio_uart(rp2350-insulin-pump 1)
pico_enable_stdio_usb(rp2350-insulin-pump 0)

# Add the standard library to the build
target_link_libraries(rp2350-insulin-pump
        pico_stdlib)

# Add the standard include files to the build
target_include_directories(rp2350-insulin-pump PRIVATE
        ${CMAKE_CURRENT_LIST_DIR}/src
)

# Add any user requested libraries
target_link_libraries(rp2350-insulin-pump
        hardware_i2c
        hardware_adc
        hardware_gpio
        hardware_timer
        )

pico_add_extra_outputs(rp2350-insulin-pump)

