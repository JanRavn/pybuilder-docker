import base64
import json
import os
import shutil

from pybuilder.core import after
from pybuilder.core import depends
from pybuilder.core import Logger
from pybuilder.core import Project
from pybuilder.core import task
from pybuilder.pluginhelper.external_command import ExternalCommandBuilder
from pybuilder.reactor import Reactor


@task(description="Package artifact into a docker container.")
@depends("publish")
def docker_package(project: Project, logger: Logger, reactor: Reactor) -> None:
    do_docker_package(project, logger, reactor)


@after("publish")
def do_docker_package(project: Project, logger: Logger, reactor: Reactor) -> None:
    project.set_property_if_unset("docker_package_build_dir", "src/main/docker")
    project.set_property_if_unset("docker_package_build_image", project.name)
    project.set_property_if_unset("docker_package_build_version", project.version)

    reactor.pybuilder_venv.verify_can_execute(
        command_and_arguments=["docker", "--version"], prerequisite="docker", caller="docker_package")

    # True if user set verbose in build.py or from command line
    verbose = project.get_property("verbose")
    project.set_property_if_unset("docker_package_verbose_output", verbose)

    report_dir = prepare_directory("$dir_reports", project)

    temp_build_img = f"pyb-temp-{project.name}:{project.version}"
    build_img = project.get_property("docker_package_build_img", f"{project.name}:{project.version}")

    # docker build --build-arg buildVersion=${BUILD_NUMBER} -t ${BUILD_IMG} src/
    command = ExternalCommandBuilder('docker', project, reactor)
    command.use_argument('build')
    command.use_argument('--build-arg')
    command.use_argument('buildVersion={0}').formatted_with_property('docker_package_build_version')
    command.use_argument('-t')
    command.use_argument('{0}').formatted_with(temp_build_img)
    command.use_argument('{0}').formatted_with_property('docker_package_build_dir')
    logger.info(f"Executing primary stage docker build for image - {build_img}.")
    result = command.run(f"{report_dir}/docker_package_build")
    if result.exit_code != 0:
        logger.error(result.error_report_lines)  # TODO verbose?

        raise Exception("Error building primary stage docker image")

    dist_dir = prepare_directory("$dir_dist", project)

    setup_script = os.path.join(dist_dir, "Dockerfile")
    with open(setup_script, "w") as setup_file:
        setup_file.write(render_docker_buildfile(project, temp_build_img))
    os.chmod(setup_script, 0o755)

    dist_file = project.get_property("docker_package_dist_file", f"{project.name}-{project.version}.tar.gz")
    dist_file_path = project.expand_path("$dir_dist", 'dist', dist_file)
    shutil.copy2(dist_file_path, dist_dir)

    command = ExternalCommandBuilder('docker', project, reactor)
    command.use_argument('build')
    command.use_argument('-t')
    command.use_argument('{0}').formatted_with(build_img)
    command.use_argument('{0}').formatted_with(dist_dir)
    logger.info(f"Executing secondary stage docker build for image - {build_img}.")
    result = command.run(f"{report_dir}/docker_package_img")
    if result.exit_code != 0:
        logger.error(result.error_report_lines)

        raise Exception("Error building docker image")

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

    if "ecr" in registry:
        _prep_ecr(project, logger, reactor, registry, fq_artifact)

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


def _prep_ecr(project: Project, logger: Logger, reactor: Reactor, registry: str, fq_artifact: str) -> None:
    _ecr_login(project, logger, reactor, registry)
    create_ecr_registry = project.get_property("ensure_ecr_registry_created", True)
    if create_ecr_registry:
        _create_ecr_registry(project, logger, reactor, fq_artifact)


# aws ecr get-authorization-token --output text --query 'authorizationData[].authorizationToken'
#     | base64 -D
#     | cut -d: -f2
# docker login -u AWS -p <my_decoded_password> -e <any_email_address> <aws_account_id>.dkr.ecr.us-west-2.amazonaws.com
def _ecr_login(project: Project, logger: Logger, reactor: Reactor, registry: str) -> None:
    reports_dir = prepare_directory("$dir_reports", project)

    command = ExternalCommandBuilder('aws', project, reactor)
    command.use_argument('ecr')
    command.use_argument('get-authorization-token')
    command.use_argument('--output')
    command.use_argument('text')
    command.use_argument('--query')
    command.use_argument('authorizationData[].authorizationToken')
    result = command.run(f"{reports_dir}/docker_ecr_get_token")
    if result.exit_code > 0:
        logger.info(result.error_report_lines)  # TODO verbose?

        raise Exception("Error getting token")

    pass_token = base64.b64decode(result.report_lines[0])
    username, password = pass_token.split(":")
    command = ExternalCommandBuilder('docker', project, reactor)
    command.use_argument('login')
    command.use_argument('-u')
    command.use_argument('{0}').formatted_with(username)
    command.use_argument('-p')
    command.use_argument('{0}').formatted_with(password)
    command.use_argument('{0}').formatted_with(registry)
    result = command.run(f"{reports_dir}/docker_ecr_docker_login")
    if result.exit_code > 0:
        logger.info(result.error_report_lines)  # TODO verbose?

        raise Exception("Error authenticating")


def _create_ecr_registry(project: Project, logger: Logger, reactor: Reactor, fq_artifact: str) -> None:
    reports_dir = prepare_directory("$dir_reports", project)

    command = ExternalCommandBuilder('aws', project, reactor)
    command.use_argument('ecr')
    command.use_argument('describe-repositories')
    command.use_argument('--repository-names')
    command.use_argument('{0}').formatted_with(fq_artifact)
    result = command.run(f"{reports_dir}/docker_ecr_registry_discover")
    if result.exit_code > 0:
        command = ExternalCommandBuilder('aws', project, reactor)
        command.use_argument('ecr')
        command.use_argument('create-repository')
        command.use_argument('--repository-name')
        command.use_argument('{0}').formatted_with(fq_artifact)
        result = command.run(f"{reports_dir}/docker_ecr_registry_create")
        if result.exit_code > 0:
            logger.info(result.error_report_lines)  # TODO verbose?

            raise Exception("Unable to create ecr registry")


# docker tag ${APPLICATION}/${ROLE} ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:${BUILD_NUMBER}
# docker tag ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:${BUILD_NUMBER} ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:latest
def _run_tag_cmd(project: Project, logger: Logger, reactor: Reactor, local_img: str, remote_img: str) -> None:
    report_dir = prepare_directory("$dir_reports", project)

    command = ExternalCommandBuilder('docker', project, reactor)
    command.use_argument('tag')
    command.use_argument('{0}').formatted_with(local_img)
    command.use_argument('{0}').formatted_with(remote_img)
    logger.info("Tagging local docker image {} - {}".format(local_img, remote_img))
    result = command.run(f"{report_dir}/docker_push_tag")
    if result.exit_code > 0:
        logger.info(result.error_report_lines)  # TODO verbose?

        raise Exception(f"Error tagging image to remote registry - {remote_img}")


# docker push ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:latest
# docker push ${DOCKER_REGISTRY}/${APPLICATION}/${ROLE}:${BUILD_NUMBER}
def _run_push_cmd(project: Project, logger: Logger, reactor: Reactor, remote_img: str) -> None:
    report_dir = prepare_directory("$dir_reports", project)

    command = ExternalCommandBuilder('docker', project, reactor)
    command.use_argument('push')
    command.use_argument('{0}').formatted_with(remote_img)
    logger.info("Pushing remote docker image - {}".format(remote_img))
    result = command.run(f"{report_dir}/docker_push_tag")
    if result.exit_code > 0:
        logger.info(result.error_report_lines)  # TODO verbose?

        raise Exception(f"Error pushing image to remote registry - {remote_img}")


def render_docker_buildfile(project: Project, build_image: str) -> str:
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

    return f"FROM {build_image}\n" \
           f"MAINTAINER {maintainer}\n" \
           f"COPY ${dist_file} .\n" \
           f"RUN ${prepare_env_cmd}\n" \
           f"RUN ${package_cmd}\n"


def prepare_directory(dir_variable: str, project: Project) -> str:
    package_format = f"{dir_variable}/docker"
    reports_dir = project.expand_path(package_format)
    if not os.path.exists(reports_dir):
        os.mkdir(reports_dir)

    return reports_dir
