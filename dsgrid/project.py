"""Interface to a dsgrid project."""

import itertools
import logging

from pyspark.sql import SparkSession

from dsgrid.dataset import Dataset
from dsgrid.dimension.base_models import DimensionType  # , MappingType
from dsgrid.dimension.store import DimensionStore
from dsgrid.exceptions import DSGInvalidField, DSGValueNotRegistered
from dsgrid.registry.registry_manager import RegistryManager, get_registry_path
from dsgrid.utils.spark import init_spark


logger = logging.getLogger(__name__)


class Project:
    """Interface to a dsgrid project."""

    def __init__(self, config, project_dim_store, dataset_configs, dataset_dim_stores):
        self._spark = SparkSession.getActiveSession()
        self._config = config
        self._project_dimension_store = project_dim_store
        self._dataset_dimension_stores = dataset_dim_stores
        self._dataset_configs = dataset_configs
        self._datasets = {}

    @classmethod
    def load(cls, project_id, registry_path=None, version=None, offline_mode=False):
        """Load a project from the registry.

        Parameters
        ----------
        project_id : str
        registry_path : str | None
        version : str | None
            Use the latest if not specified.
        offline_mode : bool
            If True, don't sync with remote registry

        """
        spark = SparkSession.getActiveSession()
        if spark is None:
            spark = init_spark("project")

        registry_path = get_registry_path(registry_path=registry_path)
        manager = RegistryManager.load(registry_path, offline_mode=offline_mode)
        dataset_manager = manager.dataset_manager
        project_manager = manager.project_manager
        registry = project_manager.get_registry_config(project_id)
        registered_datasets = registry.list_registered_datasets()
        if version is None:
            version = registry.version
        config = project_manager.get_by_id(project_id, version=version)

        project_dimension_store = DimensionStore.load(
            itertools.chain(
                config.base_dimensions.values(), config.supplemental_dimensions.values()
            ),
        )
        dataset_dim_stores = {}
        dataset_configs = {}
        for dataset_id in registered_datasets:
            dataset_config = dataset_manager.get_by_id(dataset_id)
            dataset_configs[dataset_id] = dataset_config
            dataset_dim_stores[dataset_id] = DimensionStore.load(
                dataset_config.dimensions.values()
            )

        return cls(config, project_dimension_store, dataset_configs, dataset_dim_stores)

    @property
    def config(self):
        """Returns the ProjectConfig."""
        return self._config

    @property
    def project_dimension_store(self):
        return self._project_dimension_store

    def get_dataset(self, dataset_id):
        """Returns a Dataset. Calls load_dataset if it hasn't already been loaded.

        Parameters
        ----------
        dataset_id : str

        Returns
        -------
        Dataset

        """
        if dataset_id not in self._datasets:
            raise ValueError(f"dataset {dataset_id} has not been loaded")
        return self._datasets[dataset_id]

    def load_dataset(self, dataset_id):
        """Loads a dataset. Creates a view for each of its tables.

        Parameters
        ----------
        dataset_id : str

        """
        if dataset_id not in self._dataset_configs:
            raise DSGValueNotRegistered(
                f"dataset_id={dataset_id} is not registered in the project"
            )
        config = self._dataset_configs[dataset_id]
        dataset = Dataset.load(config)
        dataset.create_views()
        self._datasets[dataset_id] = dataset

    def unload_dataset(self, dataset_id):
        """Loads a dataset. Creates a view for each of its tables.

        Parameters
        ----------
        dataset_id : str

        """
        dataset = self.get_dataset(dataset_id)
        dataset.delete_views()

    """
    The code below is subject to change.
    """

    def _iter_base_dimensions(self):
        for dimension in self.config.dimensions.base_dimensions:
            yield dimension

    def _iter_input_datasets(self):
        for dataset in self.config.input_datasets.datasets:
            yield dataset

    def list_input_datasets(self):
        return [x.dataset_id for x in self._iter_input_datasets()]

    def get_project_dimension(self, dimension_type):
        for dimension in self._iter_base_dimensions():
            if dimension.dimension_type == dimension_type:
                return dimension
        raise DSGInvalidField(f"{dimension_type} is not stored")

    def get_input_dataset(self, dataset_id):
        for dataset in self._iter_input_datasets():
            if dataset.dataset_id == dataset_id:
                return dataset
        raise DSGInvalidField(f"{dataset_id} is not stored")

    def get_geography(self):
        # TODO: is name right? cls?
        return self.get_project_dimension(DimensionType.GEOGRAPHY).name

    def get_dimension_mappings(self, dimension_type, mapping_type):
        if isinstance(mapping_type, str):
            mapping_type = MappingType(mapping_type)
        dimension = self.get_project_dimension(dimension_type)
        return getattr(dimension, mapping_type.value)
