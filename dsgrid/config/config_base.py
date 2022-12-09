import abc
import filecmp
import logging
import os
import shutil
from pathlib import Path

import json5

from dsgrid.data_models import serialize_model
from dsgrid.exceptions import DSGInvalidOperation
from dsgrid.utils.spark import models_to_dataframe


logger = logging.getLogger(__name__)


class ConfigBase(abc.ABC):
    """Base class for all config classes"""

    def __init__(self, model):
        self._model = model

    @classmethod
    def load(cls, config_file, *args, **kwargs):
        """Load the config from a file.

        Parameters
        ---------
        config_file : str

        Returns
        -------
        ConfigBase

        """
        # Subclasses can reimplement this method if they need more arguments.
        return cls._load(config_file, *args, **kwargs)

    @classmethod
    def load_from_model(cls, model):
        """Load the config from a model.

        Parameters
        ---------
        model : DSGBaseModel

        Returns
        -------
        ConfigBase

        """
        return cls(model)

    @classmethod
    def _load(cls, config_file):
        model = cls.model_class().load(config_file)
        return cls(model)

    @staticmethod
    @abc.abstractmethod
    def config_filename():
        """Return the config filename.

        Returns
        -------
        str

        """

    @property
    @abc.abstractmethod
    def config_id(self):
        """Return the configuration ID.

        Returns
        -------
        str

        """

    @property
    def model(self):
        """Return the data model for the config.

        Returns
        -------
        DSGBaseModel

        """
        return self._model

    @staticmethod
    @abc.abstractmethod
    def model_class():
        """Return the data model class backing the config"""

    def serialize(self, path, force=False):
        """Serialize the configuration to a path.

        path : str
            Directory
        force : bool
            If True, overwrite files.

        """
        filename = Path(path) / self.config_filename()
        if filename.exists() and not force:
            raise DSGInvalidOperation(f"{filename} exists. Set force=True to overwrite.")
        filename.write_text(json5.dumps(serialize_model(self.model), indent=2))
        return filename


class ConfigWithDataFilesBase(ConfigBase, abc.ABC):
    """Intermediate-level base class to provide serialization of data files."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._src_dir = None

    @staticmethod
    @abc.abstractmethod
    def data_file_fields():
        """Return the model field names that contain single data files.

        Returns
        -------
        list

        """

    @staticmethod
    @abc.abstractmethod
    def data_files_fields():
        """Return the model field names that contain multiple data files.

        Returns
        -------
        list

        """

    def get_records_dataframe(self):
        """Return the records in a spark dataframe. Cached on first call."""
        # id provides uniqueness and the config_id could help inspect what's in cache in case we
        # ever need that.
        # Spark doesn't allow dashes in the table name.
        table_name = f"{self.config_id}__{id(self)}".replace("-", "_")
        df = models_to_dataframe(self.model.records, table_name=table_name)
        logger.debug("Loaded %s records dataframe", self.config_id)
        return df

    @classmethod
    def load(cls, config_file):
        config = super().load(config_file)
        config.src_dir = os.path.dirname(config_file)
        return config

    def serialize(self, path, force=False):
        # Serialize the data alongside the config file at path and point the
        # config to that file to ensure that the config will be Pydantic-valid when loaded.
        model_data = serialize_model(self.model)
        dst_config_file = path / self.config_filename()
        if dst_config_file.exists() and not force:
            raise DSGInvalidOperation(f"{dst_config_file} exists. Set force=True to overwrite.")

        for field in self.data_file_fields():
            orig_file = getattr(self.model, field)

            # Leading directories from the original are not relevant in the registry.
            dst_data_file = Path(path) / Path(orig_file).name
            if dst_data_file.exists() and not force:
                raise DSGInvalidOperation(f"{dst_data_file} exists. Set force=True to overwrite.")

            shutil.copyfile(self._src_dir / orig_file, dst_data_file)
            # - We have to make this change in the serialized dict instead of
            #   model because Pydantic will fail the assignment due to not being
            #   able to find the path.
            # - This filename/file hack is because file is used as an alias.
            #   Hopefully this won't happen with any other fields.
            if field == "filename" and field not in model_data:
                field = "file"
            model_data[field] = Path(orig_file).name

        new_files = []
        for field in self.data_files_fields():
            for orig_file in getattr(self.model, field):
                dst_data_file = Path(path) / Path(orig_file).name
                if dst_data_file.exists() and not force:
                    raise DSGInvalidOperation(
                        f"{dst_data_file} exists. Set force=True to overwrite."
                    )
                src_data_file = self._src_dir / orig_file
                if not dst_data_file.exists() or not filecmp.cmp(src_data_file, dst_data_file):
                    shutil.copyfile(self._src_dir / orig_file, dst_data_file)
                new_files.append(Path(orig_file).name)
            model_data[field] = new_files

        dst_config_file.write_text(json5.dumps(model_data, indent=2))
        return dst_config_file

    @property
    def src_dir(self):
        """Return the directory containing the config file. Data files inside the config file
        are relative to this.

        """
        return self._src_dir

    @src_dir.setter
    def src_dir(self, src_dir):
        """Set the source directory. Must be the directory containing the config file."""
        self._src_dir = Path(src_dir)
