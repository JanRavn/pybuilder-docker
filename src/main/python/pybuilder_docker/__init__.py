import base64
import json
import os
import shutil
from typing import Optional

from pybuilder.core import after
from pybuilder.core import depends
from pybuilder.core import Logger
from pybuilder.core import Project
from pybuilder.core import task
from pybuilder.pluginhelper.external_command import ExternalCommandBuilder
from pybuilder.pluginhelper.external_command import ExternalCommandResult
from pybuilder.reactor import Reactor


def _exec_cmd(
        project: Project, logger: Logger, reactor: Reactor,
        program: str, *arguments: str,
        message: str = None, error: str = None, report_file: str = None,
        verbose_property: str = None, force_log: bool = False,
) -> Optional[ExternalCommandResult]:
    report_folder = project.expand_path("$dir_reports/docker")
    if not os.path.exists(report_folder):
        os.mkdir(report_folder)
    report_file = report_file or "_".join([program, *arguments])

    command = ExternalCommandBuilder(program, project, reactor)
    for argument in arguments:
        command.use_argument(argument)
    if message:
        logger.info(message)
    result = command.run(f"{report_folder}/{report_file}")
    if result.exit_code == 0:
        return result

    is_verbose = project.get_property("verbose", False)
    is_verbose_property = verbose_property and project.get_property(verbose_property, False)
    if force_log or is_verbose or is_verbose_property:
        logger.error(result.error_report_lines)
    if error:
        raise Exception(error)


@task(description="Package artifact into a docker container.")
@depends("publish")
def docker_package(project: Project, logger: Logger, reactor: Reactor) -> None:
    do_docker_package(project, logger, reactor)


@after("publish")
def do_docker_package(project: Project, logger: Logger, reactor: Reactor) -> None:
    project.set_property_if_unset("docker_package_build_dir", "src/main/docker")
    project.set_property_if_unset("docker_package_build_image", project.name)
    project.set_property_if_unset("docker_package_build_version", project.version)
    project.set_property_if_unset("docker_package_verbose_output", project.get_property("verbose"))

    reactor.pybuilder_venv.verify_can_execute(
        command_and_arguments=["docker", "--version"], prerequisite="docker", caller="docker_package")

    temp_build_img = f"pyb-temp-{project.name}:{project.version}"
    build_img = project.get_property("docker_package_build_img", f"{project.name}:{project.version}")

    # docker build --build-arg buildVersion=${BUILD_NUMBER} -t ${BUILD_IMG} src/
    _exec_cmd(
        project, logger, reactor,
        'docker', 'build',
        '--build-arg', f'buildVersion={project.get_property("docker_package_build_version")}',
        '-t', temp_build_img, project.get_property('docker_package_build_dir'),
        message=f"Executing primary stage docker build for image - {build_img}.",
        error="Error building primary stage docker image",
        report_file="docker_package_build",
        verbose_property="docker_package_build",
        force_log=True,
    )

    dist_dir = project.expand_path("$dir_dist/docker")
    if not os.path.exists(dist_dir):
        os.mkdir(dist_dir)

    setup_script = os.path.join(dist_dir, "Dockerfile")
    with open(setup_script, "w") as setup_file:
        maintainer = project.get_property("docker_package_image_maintainer", "anonymous"),
        dist_file = project.get_property("docker_package_dist_file", f"{project.name}-{project.version}.tar.gz")
        prepare_env_cmd = project.get_property(
            "docker_package_prepare_env_cmd",
            "echo 'empty prepare_env_cmd installing into python'",
        ),
        package_cmd = project.get_property(
            "docker_package_package_cmd",
            f"pip install {dist_file}",
        )
        setup_file.write(f"FROM {temp_build_img}\n"
                         f"MAINTAINER {maintainer}\n"
                         f"COPY ${dist_file} .\n"
                         f"RUN ${prepare_env_cmd}\n"
                         f"RUN ${package_cmd}\n")
    os.chmod(setup_script, 0o755)

    dist_file = project.get_property("docker_package_dist_file", f"{project.name}-{project.version}.tar.gz")
    dist_file_path = project.expand_path("$dir_dist", 'dist', dist_file)
    shutil.copy2(dist_file_path, dist_dir)

    _exec_cmd(
        project, logger, reactor,
        'docker', 'build', '-t', build_img, dist_dir,
        message=f"Executing secondary stage docker build for image - {build_img}.",
        error="Error building docker image",
        report_file="docker_package_img",
        verbose_property="docker_package_verbose_output",
        force_log=True,
    )

    logger.info(f"Finished build docker image - {build_img} - with dist file - {dist_dir}")


@task(description="Publish artifact into a docker registry.")
@depends("docker_package")
def docker_push(project: Project, logger: Logger, reactor: Reactor) -> None:
    do_docker_push(project, logger, reactor)


def do_docker_push(project: Project, logger: Logger, reactor: Reactor) -> None:
    # True if user set verbose in build.py or from command line
    verbose = project.get_property("verbose")
    project.set_property_if_unset("docker_push_verbose_output", verbose)

    registry = project.get_mandatory_property("docker_push_registry")
    local_img = project.get_property("docker_package_build_img", f"{project.name}:{project.version}")
    fq_artifact = project.get_property("docker_push_img", local_img)
    registry_path = f"{registry}/{fq_artifact}"

    # aws ecr get-authorization-token --output text --query 'authorizationData[].authorizationToken'
    #     | base64 -D
    #     | cut -d: -f2
    # docker login -u AWS -p <my_decoded_password> -e <any_email_address>
    #     <aws_account_id>.dkr.ecr.us-west-2.amazonaws.com
    if "ecr" in registry:
        result = _exec_cmd(
            project, logger, reactor,
            'aws', 'ecr', 'get-authorization-token', '--output', 'text', '--query',
            'authorizationData[].authorizationToken',
            error="Error getting token",
            report_file="docker_ecr_get_token",
            verbose_property="docker_package_verbose_output",
        )
        username, password = base64.b64decode(result.report_lines[0]).split(":")
        _exec_cmd(
            project, logger, reactor,
            'docker', 'login', '-u', username, '-p', password, registry,
            report_file="docker_ecr_docker_login",
            error="Error authenticating",
            verbose_property="docker_package_verbose_output",
        )
        create_ecr_registry = project.get_property("ensure_ecr_registry_created", True)
        if create_ecr_registry:
            if not _exec_cmd(
                    project, logger, reactor,
                    'aws', 'ecr', 'describe-repositories', '--repository-names', fq_artifact,
                    report_file="docker_ecr_registry_discover",
                    verbose_property="docker_package_verbose_output",
            ):
                _exec_cmd(
                    project, logger, reactor,
                    'aws', 'ecr', 'create-repository', '--repository-name', fq_artifact,
                    report_file="docker_ecr_registry_create",
                    verbose_property="docker_package_verbose_output",
                )

    tags = [project.version]
    tag_as_latest = project.get_property("docker_push_tag_as_latest", True)
    if tag_as_latest:
        tags.append('latest')
    for tag in tags:
        remote_img = f"{project.name}:{tag}"
        _run_tag_cmd(project, logger, reactor, local_img, remote_img)
        _run_push_cmd(project, logger, reactor, remote_img)

    artifact_path = project.expand_path('$dir_target', 'artifact.json')
    with open(artifact_path, 'w') as target:
        artifact_manifest = {
            'artifact-type': 'container',
            'artifact-path': registry_path,
            'artifact-identifier': project.version,
        }
        json.dump(artifact_manifest, target)


# docker tag ${APPLICATION}/${ROLE} ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:${BUILD_NUMBER}
# docker tag ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:${BUILD_NUMBER} ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:latest
def _run_tag_cmd(project: Project, logger: Logger, reactor: Reactor, local_img: str, remote_img: str) -> None:
    _exec_cmd(
        project, logger, reactor,
        'docker', 'tag', local_img, remote_img,
        message=f"Tagging local docker image {local_img} - {remote_img}",
        error=f"Error tagging image to remote registry - {remote_img}",
        report_file='docker_push_tag',
        verbose_property="docker_package_verbose_output",
    )


# docker push ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:latest
# docker push ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:${BUILD_NUMBER}
def _run_push_cmd(project: Project, logger: Logger, reactor: Reactor, remote_img: str) -> None:
    _exec_cmd(
        project, logger, reactor,
        'docker', 'push', remote_img,
        message=f"Pushing remote docker image - {remote_img}",
        error=f"Error pushing image to remote registry - {remote_img}",
        report_file="docker_push_tag",
        verbose_property="docker_package_verbose_output",
        force_log=True,
    )
