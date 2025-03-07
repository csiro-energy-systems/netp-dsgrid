import re
from pathlib import Path

from click.testing import CliRunner

from dsgrid.cli.dsgrid import cli
from dsgrid.cli.dsgrid_admin import cli as admin_cli
from dsgrid.registry.registry_database import DatabaseConnection
from dsgrid.registry.registry_manager import RegistryManager
from dsgrid.utils.files import load_data
from dsgrid.tests.common import TEST_DATASET_DIRECTORY
from dsgrid.tests.common import (
    map_dimension_names_to_ids,
    replace_dimension_names_with_current_ids,
)


def test_register_dimensions_and_mappings(tmp_registry_db):
    src_dir, tmpdir, db_name = tmp_registry_db
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(admin_cli, ["create-registry", db_name, "-p", str(tmpdir), "--force"])
    assert result.exit_code == 0
    project_dimension_mapping_config = src_dir / "dimension_mappings_with_ids.json5"

    dim_config_file = src_dir / "dimensions.json5"
    cmd = [
        "--database-name",
        db_name,
        "--offline",
        "registry",
        "dimensions",
        "register",
        str(dim_config_file),
        "-l",
        "log",
    ]
    result = runner.invoke(cli, cmd)
    assert result.exit_code == 0
    conn = DatabaseConnection(database=db_name)
    manager = RegistryManager.load(conn, offline_mode=True)
    mappings = map_dimension_names_to_ids(manager.dimension_manager)
    replace_dimension_names_with_current_ids(project_dimension_mapping_config, mappings)

    # Registering duplicates is allowed.
    result = runner.invoke(cli, cmd)

    cmd = [
        "--database-name",
        db_name,
        "--offline",
        "registry",
        "dimension-mappings",
        "register",
        str(project_dimension_mapping_config),
        "-l",
        "log",
    ]
    result = runner.invoke(cli, cmd)
    assert result.exit_code == 0
    result = runner.invoke(cli, cmd)
    assert result.exit_code == 0


def test_register_project_and_dataset(tmp_registry_db):
    src_dir, tmpdir, db_name = tmp_registry_db
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(admin_cli, ["create-registry", db_name, "-p", str(tmpdir), "--force"])
    assert result.exit_code == 0
    dataset_dir = Path("datasets/modeled/comstock")

    project_config = src_dir / "project.json5"
    project_id = load_data(project_config)["project_id"]
    dataset_config = src_dir / dataset_dir / "dataset.json5"
    dataset_map_file = src_dir / dataset_dir / "dimension_mappings.json5"
    dataset_id = load_data(dataset_config)["dataset_id"]
    dataset_path = TEST_DATASET_DIRECTORY / dataset_id

    result = runner.invoke(
        cli,
        [
            "--database-name",
            db_name,
            "--offline",
            "registry",
            "projects",
            "register",
            str(project_config),
            "--log-message",
            "log",
        ],
    )
    conn = DatabaseConnection(database=db_name)
    manager = RegistryManager.load(conn, offline_mode=True)
    mappings = map_dimension_names_to_ids(manager.dimension_manager)
    replace_dimension_names_with_current_ids(dataset_config, mappings)
    cmd = [
        "--database-name",
        db_name,
        "--offline",
        "registry",
        "projects",
        "register-and-submit-dataset",
        "--dataset-config-file",
        str(dataset_config),
        "--dataset-path",
        str(dataset_path),
        "--dimension-mapping-file",
        str(dataset_map_file),
        "--project-id",
        project_id,
        "--log-message",
        "log",
    ]

    result = runner.invoke(cli, cmd)
    assert result.exit_code == 0

    result = runner.invoke(cli, ["--database-name", db_name, "--offline", "registry", "list"])
    assert result.exit_code == 0
    regex_project = re.compile(rf"{project_id}.*1\.1\.0")
    regex_dataset = re.compile(rf"{dataset_id}.*1\.0\.0")
    assert regex_project.search(result.stdout) is not None, result.stdout
    assert regex_dataset.search(result.stdout) is not None, result.stdout
    dim_id = manager.dimension_manager.list_ids()[0]
    dim_map_id = manager.dimension_mapping_manager.list_ids()[0]

    result = runner.invoke(
        admin_cli,
        ["--database-name", db_name, "--offline", "registry", "projects", "remove", project_id],
    )
    assert result.exit_code == 0
    result = runner.invoke(
        admin_cli,
        ["--database-name", db_name, "--offline", "registry", "datasets", "remove", dataset_id],
    )
    assert result.exit_code == 0
    result = runner.invoke(
        admin_cli,
        [
            "--database-name",
            db_name,
            "--offline",
            "registry",
            "dimension-mappings",
            "remove",
            dim_map_id,
        ],
    )
    assert result.exit_code == 0
    result = runner.invoke(
        admin_cli,
        ["--database-name", db_name, "--offline", "registry", "dimensions", "remove", dim_id],
    )
    assert result.exit_code == 0
