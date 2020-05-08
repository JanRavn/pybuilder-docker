import os
from unittest import TestCase

DIRNAME = os.path.dirname(os.path.abspath(__file__))


class PybuilderDockerTestCase(TestCase):
    def tearDown(self):
        pass

    @classmethod
    def setUpClass(cls):
        super(PybuilderDockerTestCase, cls).setUpClass()

    def test__exec_cmd(self):
        # self.fail()
        pass

    def test__make_folder(self):
        # self.fail()
        pass

    def test_docker_package(self):
        # self.fail()
        pass

    def test_do_docker_package(self):
        # self.fail()
        pass

    def test__docker_build_stages(self):
        # self.fail()
        pass

    def test__generate_dockerfile(self):
        # self.fail()
        pass

    def test__copy_dist_package(self):
        # self.fail()
        pass

    def test_docker_push(self):
        # self.fail()
        pass

    def test__do_docker_push(self):
        # self.fail()
        pass

    def test__docker_login_aws_ecr(self):
        # self.fail()
        pass

    def test__docker_tag_and_push_image(self):
        # self.fail()
        pass

    def test__generate_artifact_manifest(self):
        # self.fail()
        pass
