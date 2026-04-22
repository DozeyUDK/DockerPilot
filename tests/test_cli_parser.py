from dockerpilot.cli.parser import build_cli_parser


def test_parser_accepts_validate_command():
    args = build_cli_parser().parse_args(["validate"])

    assert args.command == "validate"


def test_parser_accepts_deploy_config_command():
    args = build_cli_parser().parse_args(
        ["deploy", "config", "deployment.yml", "--type", "rolling"]
    )

    assert args.command == "deploy"
    assert args.deploy_action == "config"
    assert args.config_file == "deployment.yml"
    assert args.type == "rolling"


def test_parser_accepts_pipeline_create_command():
    args = build_cli_parser().parse_args(
        ["pipeline", "create", "--type", "github", "--output", ".github/workflows"]
    )

    assert args.command == "pipeline"
    assert args.pipeline_action == "create"
    assert args.type == "github"


def test_parser_accepts_build_fallback_flags():
    args = build_cli_parser().parse_args(
        ["build", ".", "mongo:latest", "--pull-if-missing", "--generate-template", "python"]
    )

    assert args.command == "build"
    assert args.dockerfile_path == "."
    assert args.tag == "mongo:latest"
    assert args.pull_if_missing is True
    assert args.generate_template == "python"


def test_parser_accepts_container_rename_command():
    args = build_cli_parser().parse_args(["container", "rename", "myapp", "myapp-v2"])

    assert args.command == "container"
    assert args.container_action == "rename"
    assert args.name == "myapp"
    assert args.new_name == "myapp-v2"
