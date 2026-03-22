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
