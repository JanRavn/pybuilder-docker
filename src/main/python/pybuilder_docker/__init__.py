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
    report_folder = _make_folder(project, "$dir_reports", "docker")
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


def _make_folder(project: Project, *path: str) -> str:
    folder = project.expand_path(*path)
    if not os.path.exists(folder):
        os.mkdir(folder)

    return folder


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

    dist_dir = _make_folder(project, "$dir_dist", "docker")
    build_img = project.get_property("docker_package_build_img", f"{project.name}:{project.version}")
    _docker_build_stages(project, logger, reactor, dist_dir, build_img)
    logger.info(f"Finished build docker image - {build_img} - with dist file - {dist_dir}")


# docker build --build-arg buildVersion=${BUILD_NUMBER} -t ${BUILD_IMG} src/
def _docker_build_stages(project: Project, logger: Logger, reactor: Reactor, dist_dir: str, build_img: str) -> None:
    _exec_cmd(
        project, logger, reactor,
        'docker', 'build',
        '--build-arg', f'buildVersion={project.get_property("docker_package_build_version")}',
        '-t', f"pyb-temp-{project.name}:{project.version}", project.get_property('docker_package_build_dir'),
        message=f"Executing primary stage docker build for image - {build_img}.",
        error="Error building primary stage docker image",
        report_file="docker_package_build",
        verbose_property="docker_package_build",
        force_log=True,
    )
    _generate_dockerfile(project, logger, reactor, dist_dir)
    _copy_dist_package(project, logger, reactor, dist_dir)
    _exec_cmd(
        project, logger, reactor,
        'docker', 'build', '-t', build_img, dist_dir,
        message=f"Executing secondary stage docker build for image - {build_img}.",
        error="Error building docker image",
        report_file="docker_package_img",
        verbose_property="docker_package_verbose_output",
        force_log=True,
    )


def _generate_dockerfile(project: Project, logger: Logger, reactor: Reactor, dist_dir: str) -> None:
    dist_file = project.get_property("docker_package_dist_file", f"{project.name}-{project.version}.tar.gz")
    prepare_env_cmd = project.get_property("docker_package_prepare_env_cmd",
                                           "echo 'empty prepare_env_cmd installing into python'"),
    package_cmd = project.get_property("docker_package_package_cmd", f"pip install {dist_file}")

    setup_script = os.path.join(dist_dir, "Dockerfile")
    with open(setup_script, "w") as setup_file:
        setup_file.write(f"FROM pyb-temp-{project.name}:{project.version}\n"
                         f"MAINTAINER {project.get_property('docker_package_image_maintainer', 'anonymous')}\n"
                         f"COPY ${dist_file} .\n"
                         f"RUN ${prepare_env_cmd}\n"
                         f"RUN ${package_cmd}\n")
    os.chmod(setup_script, 0o755)


def _copy_dist_package(project: Project, logger: Logger, reactor: Reactor, dist_dir: str) -> None:
    dist_file_path = project.expand_path(
        _make_folder(project, "$dir_dist", 'dist'),
        project.get_property("docker_package_dist_file", f"{project.name}-{project.version}.tar.gz")
    )
    shutil.copy2(dist_file_path, dist_dir)


@task(description="Publish artifact into a docker registry.")
@depends("docker_package")
def docker_push(project: Project, logger: Logger, reactor: Reactor) -> None:
    _do_docker_push(project, logger, reactor)


def _do_docker_push(project: Project, logger: Logger, reactor: Reactor) -> None:
    project.set_property_if_unset("docker_push_verbose_output", project.get_property("verbose"))

    registry = project.get_mandatory_property("docker_push_registry")
    local_img = project.get_property("docker_package_build_img", f"{project.name}:{project.version}")
    fq_artifact = project.get_property("docker_push_img", local_img)
    registry_path = f"{registry}/{fq_artifact}"

    _docker_login_aws_ecr(project, logger, reactor, registry, fq_artifact)
    _docker_tag_and_push_image(project, logger, reactor, local_img)
    _generate_artifact_manifest(project, logger, reactor, registry_path)


# aws ecr get-authorization-token --output text --query 'authorizationData[].authorizationToken'|base64 -D|cut -d: -f2
# docker login -u AWS -p <my_decoded_password> -e <any_email_address> <aws_account_id>.dkr.ecr.us-west-2.amazonaws.com
def _docker_login_aws_ecr(project: Project, logger: Logger, reactor: Reactor, registry: str, fq_artifact: str) -> None:
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


# docker tag ${APPLICATION}/${ROLE} ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:${BUILD_NUMBER}
# docker tag ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:${BUILD_NUMBER} ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:latest
#
# docker push ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:latest
# docker push ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:${BUILD_NUMBER}
def _docker_tag_and_push_image(project: Project, logger: Logger, reactor: Reactor, local_img: str) -> None:
    tags = [project.version]
    tag_as_latest = project.get_property("docker_push_tag_as_latest", True)
    if tag_as_latest:
        tags.append('latest')
    for tag in tags:
        remote_img = f"{project.name}:{tag}"
        _exec_cmd(
            project, logger, reactor,
            'docker', 'tag', local_img, remote_img,
            message=f"Tagging local docker image {local_img} - {remote_img}",
            error=f"Error tagging image to remote registry - {remote_img}",
            report_file='docker_push_tag',
            verbose_property="docker_package_verbose_output",
        )
        _exec_cmd(
            project, logger, reactor,
            'docker', 'push', remote_img,
            message=f"Pushing remote docker image - {remote_img}",
            error=f"Error pushing image to remote registry - {remote_img}",
            report_file="docker_push_tag",
            verbose_property="docker_package_verbose_output",
            force_log=True,
        )


def _generate_artifact_manifest(project: Project, logger: Logger, reactor: Reactor, registry_path: str) -> None:
    artifact_path = project.expand_path('$dir_target', 'artifact.json')
    with open(artifact_path, 'w') as target:
        artifact_manifest = {
            'artifact-type': 'container',
            'artifact-path': registry_path,
            'artifact-identifier': project.version,
        }
        json.dump(artifact_manifest, target, indent=4)
