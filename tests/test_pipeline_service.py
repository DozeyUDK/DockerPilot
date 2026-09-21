from dockerpilot.services.pipeline import create_pipeline_config


class FakeConsole:
    def __init__(self):
        self.messages = []

    def print(self, message):
        self.messages.append(str(message))


class FakeLogger:
    def __init__(self):
        self.messages = []

    def error(self, message):
        self.messages.append(str(message))


def test_pipeline_generators_copy_packaged_templates(tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "github-actions.yml.template").write_text("github\n", encoding="utf-8")
    (templates / "gitlab-ci.yml.template").write_text("gitlab\n", encoding="utf-8")
    (templates / "jenkinsfile.template").write_text("jenkins\n", encoding="utf-8")

    github_dir = tmp_path / "github"
    gitlab_dir = tmp_path / "gitlab"
    jenkins_dir = tmp_path / "jenkins"
    gitlab_dir.mkdir()
    jenkins_dir.mkdir()

    console = FakeConsole()
    logger = FakeLogger()
    assert create_pipeline_config(console, logger, "github", str(github_dir), templates_dir=templates)
    assert create_pipeline_config(console, logger, "gitlab", str(gitlab_dir), templates_dir=templates)
    assert create_pipeline_config(console, logger, "jenkins", str(jenkins_dir), templates_dir=templates)

    assert (github_dir / "docker-pilot.yml").read_text(encoding="utf-8") == "github\n"
    assert (gitlab_dir / ".gitlab-ci.yml").read_text(encoding="utf-8") == "gitlab\n"
    assert (jenkins_dir / "Jenkinsfile").read_text(encoding="utf-8") == "jenkins\n"


def test_pipeline_generator_rejects_unknown_type(tmp_path):
    console = FakeConsole()
    assert not create_pipeline_config(console, FakeLogger(), "unknown", str(tmp_path))
    assert any("Unsupported pipeline type" in message for message in console.messages)
