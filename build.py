from pybuilder.core import init
from pybuilder.core import use_plugin

use_plugin("python.core")
use_plugin("python.unittest")
use_plugin("python.install_dependencies")
use_plugin("python.flake8")
use_plugin("python.coverage")
use_plugin("python.distutils")

author = "stefano-bragaglia"
home_page = "https://github.com/stefano-bragaglia/pybuilder-docker"
url = "https://github.com/stefano-bragaglia/pybuilder-docker"

name = "pybuilder-docker"
description = "A pybuilder plugin that stages a python package into a docker container " \
              "and optionally publishes it to a registry."
summary = "A pybuilder plugin that stages a python package into a docker container " \
          "and optionally publishes it to a registry."
version = "0.1.0"
license = "Apache 2.0"

default_task = "publish"


@init
def set_properties(project):
    if not project.version:
        build_number = project.get_property("build_number")
        if build_number is not None and "" != build_number:
            project.version = build_number
        else:
            project.version = "0.0.999"

    project.set_property("flake8_break_build", True)  # default is False
    project.set_property("flake8_verbose_output", True)  # default is False
    project.set_property("flake8_radon_max", 10)  # default is None
    project.set_property_if_unset("flake8_max_complexity", 10)  # default is None
    # Complexity: <= 10 is easy, <= 20 is complex, <= 50 great difficulty, > 50 unmaintainable

    project.set_property("coverage_break_build", True)  # default is False
    project.set_property("coverage_verbose_output", True)  # default is False
    project.set_property("coverage_allow_non_imported_modules", False)  # default is True
    project.set_property("coverage_exceptions", [
        "__init__",
    ])
    project.set_property("coverage_threshold_warn", 35)  # default is 70
    project.set_property("coverage_branch_threshold_warn", 0)  # default is 0
    project.set_property("coverage_branch_partial_threshold_warn", 0)  # default is 0

    project.set_property("dir_source_unittest_python", "src/unittest/python")
    project.set_property("unittest_module_glob", "test_*")

    # Build and test settings
    project.set_property("run_unit_tests_propagate_stdout", True)
    project.set_property("run_unit_tests_propagate_stderr", True)

    project.set_property("distutils_upload_repository", "pypi")

    # project.depends_on_requirements("requirements.txt")
