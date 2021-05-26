import os
import re
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
import pytest

from dsgrid.exceptions import DSGDuplicateValueRegistered
from dsgrid.registry.common import DatasetRegistryStatus
from dsgrid.registry.registry_manager import RegistryManager
from dsgrid.tests.common import create_local_test_registry, make_test_project_dir
from tests.common import (
    replace_dimension_mapping_uuids_from_registry,
    replace_dimension_uuids_from_registry,
)


def test_register_project_and_dataset(make_test_project_dir):
    with TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)
        path = create_local_test_registry(base_dir)
        dataset_dir = Path("datasets/sector_models/comstock")
        dataset_dim_dir = dataset_dir / "dimensions"
        # TODO: The data repo currently does not have valid dimension mappings.
        # Disabling these tests.
        dimension_mapping_config = make_test_project_dir / dataset_dir / "dimension_mappings.toml"
        dimension_mapping_refs = (
            make_test_project_dir / dataset_dir / "dimension_mapping_references.toml"
        )

        user = "test_user"
        log_message = "registration message"
        manager = RegistryManager.load(path, offline_mode=True)
        for dim_config_file in (
            make_test_project_dir / "dimensions.toml",
            make_test_project_dir / dataset_dir / "dimensions.toml",
        ):
            dim_mgr = manager.dimension_manager
            dim_mgr.register(dim_config_file, user, log_message)
            assert dim_mgr.list_ids()
            if dim_config_file == make_test_project_dir / "dimensions.toml":
                # The other one has time only - no records.
                with pytest.raises(DSGDuplicateValueRegistered):
                    dim_mgr.register(dim_config_file, user, log_message)

        dim_mapping_mgr = manager.dimension_mapping_manager
        dim_mapping_mgr.register(dimension_mapping_config, user, log_message)
        assert dim_mapping_mgr.list_ids()
        with pytest.raises(DSGDuplicateValueRegistered):
            dim_mapping_mgr.register(dimension_mapping_config, user, log_message)

        project_config_file = make_test_project_dir / "project.toml"
        dataset_config_file = make_test_project_dir / dataset_dir / "dataset.toml"
        # replace_dimension_mapping_uuids_from_registry(
        #    path, (project_config_file, dimension_mapping_refs)
        # )
        replace_dimension_uuids_from_registry(path, (project_config_file, dataset_config_file))

        project_mgr = manager.project_manager
        project_mgr.register(project_config_file, user, log_message)
        project_id = "test"
        assert project_mgr.list_ids() == [project_id]
        project_config = project_mgr.get_by_id(project_id)

        dataset_mgr = manager.dataset_manager
        dataset_mgr.register(dataset_config_file, user, log_message)
        dataset_id = "efs_comstock"
        assert dataset_mgr.list_ids() == [dataset_id]
        dataset_config = dataset_mgr.get_by_id(dataset_id)

        project_mgr.submit_dataset(
            project_config.model.project_id,
            dataset_config.model.dataset_id,
            # [],  # TODO: this doesn't work being empty at the moment?
            [dimension_mapping_refs],  # TODO: this also doesn't work because of [[references]]
            user,
            log_message,
        )
        project_config = project_mgr.get_by_id(project_id)
        dataset = project_config.get_dataset(dataset_id)
        assert dataset.status == DatasetRegistryStatus.REGISTERED
