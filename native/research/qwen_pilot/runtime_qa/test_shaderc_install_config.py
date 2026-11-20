# SPDX-License-Identifier: Apache-2.0
"""Reproduce the pinned Shaderc install-export interaction without building C++."""

from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


# Reduced target graph from Shaderc b64fd42 third_party/CMakeLists.txt:23-24,89-97,
# SPIRV-Tools ef96ed7 CMakeLists.txt:70-73 and source/opt/CMakeLists.txt:291-303,
# and glslang e1b562a CMakeLists.txt:33,93,118,389-426, SPIRV/CMakeLists.txt:92-106,
# and glslang/CMakeLists.txt:193-197,253-257.
# Interface libraries preserve CMake's export dependency check without a compiler.
FIXTURE = """\
cmake_minimum_required(VERSION 3.22.1)
project(shaderc_install_config LANGUAGES NONE)

option(SHADERC_SKIP_INSTALL "Skip installation" ${SHADERC_SKIP_INSTALL})
option(SHADERC_SKIP_TESTS "Skip building tests" ${SHADERC_SKIP_TESTS})
option(SHADERC_SKIP_EXAMPLES "Skip building examples" ${SHADERC_SKIP_EXAMPLES})
set(SKIP_GLSLANG_INSTALL ${SHADERC_SKIP_INSTALL})
set(SKIP_SPIRV_TOOLS_INSTALL ${SHADERC_SKIP_INSTALL})
option(SKIP_SPIRV_TOOLS_INSTALL "Skip installation" ${SKIP_SPIRV_TOOLS_INSTALL})
if(NOT ${SKIP_SPIRV_TOOLS_INSTALL})
  set(ENABLE_SPIRV_TOOLS_INSTALL ON)
endif()

add_library(SPIRV-Tools-opt INTERFACE)
if(ENABLE_SPIRV_TOOLS_INSTALL)
  install(TARGETS SPIRV-Tools-opt EXPORT SPIRV-Tools-optTargets)
  install(EXPORT SPIRV-Tools-optTargets DESTINATION cmake)
endif()

set(GLSLANG_ENABLE_INSTALL $<NOT:${SKIP_GLSLANG_INSTALL}>)
option(GLSLANG_ENABLE_INSTALL "Enable glslang installation" OFF)
include(CMakeDependentOption)
option(ENABLE_SPIRV "Enables SPIRV output support" ON)
cmake_dependent_option(ENABLE_OPT "Enables spirv-opt capability if present" ON "ENABLE_SPIRV" OFF)
add_library(glslang INTERFACE)
add_library(SPIRV INTERFACE)
if(ENABLE_OPT)
  target_link_libraries(glslang INTERFACE SPIRV-Tools-opt)
  target_link_libraries(SPIRV INTERFACE glslang SPIRV-Tools-opt)
endif()
if(GLSLANG_ENABLE_INSTALL)
  install(TARGETS glslang SPIRV EXPORT glslang-targets)
  install(EXPORT glslang-targets DESTINATION cmake)
endif()
if(NOT ENABLE_OPT)
  message(FATAL_ERROR "Optimizer support must remain enabled")
endif()
"""


WORKFLOW = Path(".github/workflows/sign-qwen-backend-app-smoke.yml")
workflow_override = None


def workflow_path():
    if workflow_override is not None:
        return workflow_override
    for directory in Path(__file__).resolve().parents:
        candidate = directory / WORKFLOW
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Cannot find {WORKFLOW} above {__file__}")


class ShadercInstallConfigTest(unittest.TestCase):
    def test_host_compiler_configures_with_optimizer_exports(self):
        command = re.search(
            r"(?m)^[ \t]*cmake -S shaderc -B [^\n]*\\\n(?:[ \t]+-D[^\n]+\n)+",
            workflow_path().read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(command, "Missing host Shaderc configure command")
        options = re.findall(r"-D[^\s\\]+", command.group())
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "CMakeLists.txt").write_text(FIXTURE, encoding="utf-8")
            configure = subprocess.run(
                ["cmake", "-S", str(source), "-B", str(source.parent / "build"), "-G", "Ninja", *options],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        self.assertEqual(configure.returncode, 0, configure.stdout)


if __name__ == "__main__":
    if len(sys.argv) == 2 and not sys.argv[1].startswith("-"):
        workflow_override = Path(sys.argv.pop())
    unittest.main()
