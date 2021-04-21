"""Manages a dsgrid registry."""

import getpass
import logging

import click

from dsgrid.common import S3_REGISTRY, LOCAL_REGISTRY
from dsgrid.loggers import setup_logging
from dsgrid.registry.common import VersionUpdateType
from dsgrid.filesytem import aws
from dsgrid.registry.common import REGISTRY_LOG_FILE
from dsgrid.registry.registry_manager import RegistryManager


logger = logging.getLogger(__name__)


@click.group()
@click.option(
    "--path",
    default=LOCAL_REGISTRY,  # TEMPORARY: S3_REGISTRY is not yet supported
    show_default=True,
    envvar="DSGRID_REGISTRY_PATH",
    help="path to dsgrid registry. Override with the environment variable DSGRID_REGISTRY_PATH",
)
@click.pass_context
def registry(ctx, path):
    """Manage a registry."""
    # We want to keep a log of items that have been registered on the
    # current system. But we probably don't want this to grow forever.
    # Consider truncating or rotating.
    setup_logging("dsgrid", REGISTRY_LOG_FILE, mode="a")


@click.command()
@click.argument("registry_path")
def create(registry_path):
    """Create a new registry."""
    RegistryManager.create(registry_path)


@click.command(name="list")
@click.pass_context
# TODO: options for only projects or datasets
def list_(ctx):
    """List the contents of a registry."""
    registry_path = ctx.parent.params["path"]
    manager = RegistryManager.load(registry_path)
    print(f"Registry: {registry_path}")
    print("Projects:")
    for project in manager.list_projects():
        print(f"  - {project}")
    print("\nDatasets:")
    for dataset in manager.list_datasets():
        print(f"  - {dataset}")
    print("\nDimensions:")
    manager.dimension_manager.show()
    manager.dimension_mapping_manager.show()


@click.group()
@click.pass_context
def projects(ctx):
    """Project subcommands"""


@click.group()
@click.pass_context
def datasets(ctx):
    """Dataset subcommands"""


@click.group()
@click.pass_context
def dimensions(ctx):
    """Dimension subcommands"""


@click.group()
@click.pass_context
def dimension_mappings(ctx):
    """Dimension mapping subcommands"""


@click.command(name="list")
@click.pass_context
def list_dimensions(ctx):
    """List the registered dimensions."""
    registry_path = ctx.parent.parent.params["path"]
    manager = RegistryManager.load(registry_path).dimension_manager
    manager.show()


@click.command(name="list")
@click.pass_context
def list_dimension_mappings(ctx):
    """List the registered dimension mappings."""
    registry_path = ctx.parent.parent.params["path"]
    manager = RegistryManager.load(registry_path).dimension_mapping_manager
    manager.show()


@click.command(name="remove")
@click.argument("project-id")
@click.pass_context
def remove_project(ctx, project_id):
    """Remove a project from the dsgrid repository."""
    registry_path = ctx.parent.parent.params["path"]
    manager = RegistryManager.load(registry_path)
    manager.remove_project(project_id)


@click.command(name="register")
@click.argument("project-config-file")
@click.option(
    "-l",
    "--log-message",
    required=True,
    help="reason for submission",
)
@click.pass_context
def register_project(ctx, project_config_file, log_message):
    """Register a new project with the dsgrid repository."""
    registry_path = ctx.parent.parent.params["path"]
    manager = RegistryManager.load(registry_path)
    submitter = getpass.getuser()
    manager.register_project(project_config_file, submitter, log_message)


@click.command(name="register")
@click.argument("dimension-config-file")
@click.option(
    "-l",
    "--log-message",
    required=True,
    help="reason for submission",
)
@click.pass_context
def register_dimensions(ctx, dimension_config_file, log_message):
    """Register new dimensions with the dsgrid repository."""
    registry_path = ctx.parent.parent.params["path"]
    manager = RegistryManager.load(registry_path).dimension_manager
    submitter = getpass.getuser()
    manager.register(dimension_config_file, submitter, log_message)


@click.command(name="register")
@click.argument("dimension-mapping-config-file")
@click.option(
    "--force",
    default=False,
    is_flag=True,
    show_default=True,
    help="Register the dimension mappings even if there are duplicates",
)
@click.option(
    "-l",
    "--log-message",
    required=True,
    help="reason for submission",
)
@click.pass_context
def register_dimension_mappings(ctx, dimension_mapping_config_file, log_message, force):
    """Register new dimension mappings with the dsgrid repository."""
    registry_path = ctx.parent.parent.params["path"]
    submitter = getpass.getuser()
    mgr = RegistryManager.load(registry_path).dimension_mapping_manager
    mgr.register(dimension_mapping_config_file, submitter, log_message, force=force)


@click.command(name="update")
@click.argument("project-config-file")
@click.option(
    "-l",
    "--log-message",
    required=True,
    type=str,
    help="reason for submission",
)
@click.option(
    "-t",
    "--update-type",
    required=True,
    type=click.Choice([x.value for x in VersionUpdateType]),
    callback=lambda ctx, x: VersionUpdateType(x),
)
@click.pass_context
def update_project(ctx, project_config_file, log_message, update_type):
    """Update an existing project registry."""
    registry_path = ctx.parent.parent.params["path"]
    manager = RegistryManager.load(registry_path)
    submitter = getpass.getuser()
    manager.update_project(project_config_file, submitter, update_type, log_message)


@click.command()
@click.argument("dataset-config-file")
@click.option(
    "-p",
    "--project-id",
    required=True,
    type=str,
    help="project identifier",
)
@click.option(
    "-m",
    "--dimension-mapping-files",
    type=click.Path(exists=True),
    multiple=True,
    show_default=True,
    help="dimension mapping file(s)",
)
@click.option(
    "-l",
    "--log-message",
    required=True,
    type=str,
    help="reason for submission",
)
@click.pass_context
def submit(ctx, dataset_config_file, project_id, dimension_mapping_files, log_message):
    """Submit a new dataset to a dsgrid project."""
    registry_path = ctx.parent.parent.params["path"]
    manager = RegistryManager.load(registry_path)
    submitter = getpass.getuser()
    manager.submit_dataset(
        dataset_config_file, project_id, dimension_mapping_files, submitter, log_message
    )


# TODO: When resubmitting an existing dataset to a project, is that a new command or an extension
# of submit_dataset?
# TODO: update_dataset


@click.command(name="remove")
@click.argument("dataset-id")
@click.pass_context
def remove_dataset(ctx, dataset_id):
    """Remove a dataset from the dsgrid repository."""
    registry_path = ctx.parent.parent.params["path"]
    manager = RegistryManager.load(registry_path)
    manager.remove_dataset(dataset_id)


@click.command()
@click.pass_context
def sync(ctx):
    """Sync the official dsgrid registry to the local system."""
    registry_path = ctx.parent.params["path"]
    aws.sync(S3_REGISTRY, registry_path)


projects.add_command(register_project)
projects.add_command(remove_project)
projects.add_command(update_project)

datasets.add_command(submit)
datasets.add_command(remove_dataset)

dimensions.add_command(register_dimensions)
dimensions.add_command(list_dimensions)

dimension_mappings.add_command(register_dimension_mappings)
dimension_mappings.add_command(list_dimension_mappings)

registry.add_command(create)
registry.add_command(list_)
registry.add_command(projects)
registry.add_command(datasets)
registry.add_command(dimensions)
registry.add_command(dimension_mappings)
registry.add_command(sync)
