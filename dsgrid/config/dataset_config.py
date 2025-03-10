import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from pydantic import Field
from pydantic import validator
import pyspark.sql.functions as F

from dsgrid.dimension.base_models import DimensionType, check_timezone_in_geography
from dsgrid.exceptions import DSGInvalidParameter
from dsgrid.registry.common import check_config_id_strict
from dsgrid.data_models import DSGBaseModel, DSGEnum, EnumValue
from dsgrid.exceptions import DSGInvalidDimension
from dsgrid.utils.utilities import check_uniqueness
from .config_base import ConfigBase
from .dimensions import (
    DimensionReferenceModel,
    DimensionModel,
    handle_dimension_union,
)


# Note that there is special handling for S3 at use sites.
ALLOWED_LOAD_DATA_FILENAMES = ("load_data.parquet", "load_data.csv", "table.parquet")
ALLOWED_LOAD_DATA_LOOKUP_FILENAMES = (
    "load_data_lookup.parquet",
    # The next two are only used for test data.
    "load_data_lookup.csv",
    "load_data_lookup.json",
)
ALLOWED_DATA_FILES = ALLOWED_LOAD_DATA_FILENAMES + ALLOWED_LOAD_DATA_LOOKUP_FILENAMES

logger = logging.getLogger(__name__)


def check_load_data_filename(path: str):
    """Return the load_data filename in path. Supports Parquet and CSV.

    Parameters
    ----------
    path : str

    Returns
    -------
    str

    Raises
    ------
    ValueError
        Raised if no supported load data filename exists.

    """
    if path.startswith("s3://"):
        # Only Parquet is supported on AWS.
        return path + "/load_data.parquet"

    for allowed_name in ALLOWED_LOAD_DATA_FILENAMES:
        filename = Path(path) / allowed_name
        if filename.exists():
            return str(filename)

    # Use ValueError because this gets called in Pydantic model validation.
    raise ValueError(f"no load_data file exists in {path}")


def check_load_data_lookup_filename(path: str):
    """Return the load_data_lookup filename in path. Supports Parquet, CSV, and JSON.

    Parameters
    ----------
    path : str

    Returns
    -------
    str

    Raises
    ------
    ValueError
        Raised if no supported load data lookup filename exists.

    """
    if path.startswith("s3://"):
        # Only Parquet is supported on AWS.
        return path + "/load_data_lookup.parquet"

    for allowed_name in ALLOWED_LOAD_DATA_LOOKUP_FILENAMES:
        filename = Path(path) / allowed_name
        if filename.exists():
            return str(filename)

    # Use ValueError because this gets called in Pydantic model validation.
    raise ValueError(f"no load_data_lookup file exists in {path}")


class InputDatasetType(DSGEnum):
    MODELED = "modeled"
    HISTORICAL = "historical"
    BENCHMARK = "benchmark"


class DataSchemaType(DSGEnum):
    """Data schema types."""

    STANDARD = EnumValue(
        value="standard",
        description="""
        Standard data schema with load_data and load_data_lookup tables.
        Applies to datasets for which the data are provided in full.
        """,
    )
    ONE_TABLE = EnumValue(
        value="one_table",
        description="""
        One_table data schema with load_data table.
        Typically appropriate for small, low-temporal resolution datasets.
        """,
    )


class DSGDatasetParquetType(DSGEnum):
    """Dataset parquet types."""

    LOAD_DATA = EnumValue(
        value="load_data",
        description="""
        In STANDARD data_schema_type, load_data is a file with ID, timestamp, and metric value columns.
        In ONE_TABLE data_schema_type, load_data is a file with multiple data dimension and metric value columns.
        """,
    )
    LOAD_DATA_LOOKUP = EnumValue(
        value="load_data_lookup",
        description="""
        load_data_lookup is a file with multiple data dimension columns and an ID column that maps to load_data file.
        """,
    )
    # # These are not currently supported by dsgrid but may be needed in the near future
    # DATASET_DIMENSION_MAPPING = EnumValue(
    #     value="dataset_dimension_mapping",
    #     description="",
    # )  # optional
    # PROJECT_DIMENSION_MAPPING = EnumValue(
    #     value="project_dimension_mapping",
    #     description="",
    # )  # optional


class DataClassificationType(DSGEnum):
    """Data risk classification type.

    See https://uit.stanford.edu/guide/riskclassifications for more information.
    """

    # TODO: can we get NREL/DOE definitions for these instead of standford's?

    LOW = EnumValue(
        value="low",
        description="Low risk data that does not require special data management",
    )
    MODERATE = EnumValue(
        value="moderate",
        description=(
            "The moderate class includes all data under an NDA, data classified as business sensitive, "
            "data classification as Critical Energy Infrastructure Infromation (CEII), or data with Personal Identifiable Information (PII)."
        ),
    )


class StandardDataSchemaModel(DSGBaseModel):
    load_data_column_dimension: DimensionType = Field(
        title="load_data_column_dimension",
        description="The data dimension for which its values are in column form (pivoted) in the load_data table.",
    )


class OneTableDataSchemaModel(DSGBaseModel):
    load_data_column_dimension: DimensionType = Field(
        title="load_data_column_dimension",
        description="The data dimension for which its values are in column form (pivoted) in the load_data table.",
    )


class DatasetQualifierType(DSGEnum):
    QUANTITY = "quantity"
    GROWTH_RATE = "growth_rate"


class GrowthRateType(DSGEnum):
    EXPONENTIAL_ANNUAL = "exponential_annual"
    EXPONENTIAL_MONTHLY = "exponential_monthly"


class GrowthRateModel(DSGBaseModel):
    growth_rate_type: GrowthRateType = Field(
        title="growth_rate_type",
        description="Type of growth rates, e.g., exponential_annual",
        options=GrowthRateType.format_for_docs(),
    )


class DatasetConfigModel(DSGBaseModel):
    """Represents dataset configurations."""

    dataset_id: str = Field(
        title="dataset_id",
        description="Unique dataset identifier.",
        requirements=(
            "When registering a dataset to a project, the dataset_id must match the expected ID "
            "defined in the project config.",
            "For posterity, dataset_id cannot be the same as the ``data_source``"
            " (e.g., dataset cannot be 'ComStock')",
        ),
        updateable=False,
    )
    version: Optional[str] = Field(
        title="version",
        description="Version, generated by dsgrid",
        dsg_internal=True,
        updateable=False,
    )
    dataset_type: InputDatasetType = Field(
        title="dataset_type",
        description="Input dataset type.",
        options=InputDatasetType.format_for_docs(),
    )
    dataset_qualifier: DatasetQualifierType = Field(
        title="dataset_qualifier",
        description="What type of values the dataset represents (e.g., growth_rate, quantity)",
        options=DatasetQualifierType.format_for_docs(),
        default="quantity",
    )
    dataset_qualifier_metadata: Optional[GrowthRateModel] = Field(
        title="dataset_qualifier_metadata",
        description="Additional metadata to include related to the dataset_qualifier",
    )
    sector_description: Optional[str] = Field(
        title="sector_description",
        description="Sectoral description (e.g., residential, commercial, industrial, "
        "transportation, electricity)",
    )
    data_source: str = Field(
        title="data_source",
        description="Data source name, e.g. 'ComStock'.",
        requirements=(
            "When registering a dataset to a project, the `data_source` field must match one of "
            "the dimension ID records defined by the project's base data source dimension.",
        ),
        # TODO: it would be nice to extend the description here with a CLI example of how to list the project's data source IDs.
    )
    data_schema_type: DataSchemaType = Field(
        title="data_schema_type",
        description="Discriminator for data schema",
        options=DataSchemaType.format_for_docs(),
    )
    data_schema: Any = Field(
        title="data_schema",
        description="Schema (table layouts) used for writing out the dataset",
    )
    dataset_version: Optional[str] = Field(
        title="dataset_version",
        description="The version of the dataset.",
    )
    description: str = Field(
        title="description",
        description="A detailed description of the dataset.",
    )
    origin_creator: str = Field(
        title="origin_creator",
        description="Origin data creator's name (first and last)",
    )
    origin_organization: str = Field(
        title="origin_organization",
        description="Origin organization name, e.g., NREL",
    )
    origin_contributors: List[str] = Field(
        title="origin_contributors",
        description="List of origin data contributor's first and last names"
        """ e.g., ["Harry Potter", "Ronald Weasley"]""",
        default=[],
    )
    origin_project: str = Field(
        title="origin_project",
        description="Origin project name",
        optional=True,
    )
    origin_date: str = Field(
        title="origin_date",
        description="Date the source data was generated",
    )
    origin_version: str = Field(
        title="origin_version",
        description="Version of the origin data",
    )
    source: str = Field(
        title="source",
        description="Source of the data (text description or link)",
    )
    data_classification: DataClassificationType = Field(
        title="data_classification",
        description="Data security classification (e.g., low, moderate, high)",
        options=DataClassificationType.format_for_docs(),
    )
    tags: List[str] = Field(
        title="source",
        description="List of data tags",
        default=[],
    )
    enable_unit_conversion: bool = Field(
        default=True,
        description="If the dataset uses its dimension mapping for the metric dimension to also "
        "perform unit conversion, then this value should be false.",
    )
    # This field must be listed before dimensions.
    use_project_geography_time_zone: bool = Field(
        default=False,
        description="If true, time zones will be applied from the project's geography dimension. "
        "If false, the dataset's geography dimension records must provide a time zone column.",
    )
    dimensions: List = Field(
        title="dimensions",
        description="List of dimensions that make up the dimensions of dataset. They will be "
        "automatically registered during dataset registration and then converted "
        "to dimension_references.",
        requirements=(
            "* All :class:`~dsgrid.dimension.base_models.DimensionType` must be defined",
            "* Only one dimension reference per type is allowed",
            "* Each reference is to an existing registered dimension.",
        ),
        default=[],
    )
    dimension_references: List[DimensionReferenceModel] = Field(
        title="dimensions",
        description="List of registered dimension references that make up the dimensions of dataset.",
        requirements=(
            "* All :class:`~dsgrid.dimension.base_models.DimensionType` must be defined",
            "* Only one dimension reference per type is allowed",
            "* Each reference is to an existing registered dimension.",
        ),
        default=[],
        # TODO: Add to notes - link to registering dimensions page
        # TODO: Add to notes - link to example of how to list dimensions to find existing registered dimensions
    )
    user_defined_metadata: Dict = Field(
        title="user_defined_metadata",
        description="Additional user defined metadata fields",
        default={},
    )
    trivial_dimensions: Optional[List[DimensionType]] = Field(
        title="trivial_dimensions",
        default=[],
        description="List of trivial dimensions (i.e., 1-element dimensions) that"
        " do not exist in the load_data_lookup. List the dimensions by dimension type.",
        notes=(
            "Trivial dimensions are 1-element dimensions that are not present in the parquet data"
            " columns. Instead they are added by dsgrid as an alias column.",
        ),
    )
    id: Optional[str] = Field(
        alias="_id",
        description="Registry database ID",
        dsgrid_internal=True,
    )
    key: Optional[str] = Field(
        alias="_key",
        description="Registry database key",
        dsgrid_internal=True,
    )
    rev: Optional[str] = Field(
        alias="_rev",
        description="Registry database revision",
        dsgrid_internal=True,
    )

    @validator("dataset_qualifier_metadata", pre=True)
    def check_dataset_qualifier_metadata(cls, metadata, values):
        if "dataset_qualifier" in values:
            if values["dataset_qualifier"] == DatasetQualifierType.QUANTITY:
                metadata = None
            elif values["dataset_qualifier"] == DatasetQualifierType.GROWTH_RATE:
                metadata = GrowthRateModel(**metadata)
            else:
                raise ValueError(
                    f'Cannot load dataset_qualifier_metadata model for dataset_qualifier={values["dataset_qualifier"]}'
                )
        return metadata

    @validator("data_schema", pre=True)
    def check_data_schema(cls, schema, values):
        """Check and deserialize model for data_schema"""
        if "data_schema_type" in values:
            # placeholder for when there's more data_schema_type
            if values["data_schema_type"] == DataSchemaType.STANDARD:
                schema = StandardDataSchemaModel(**schema)
            elif values["data_schema_type"] == DataSchemaType.ONE_TABLE:
                schema = OneTableDataSchemaModel(**schema)
            else:
                raise ValueError(
                    f'Cannot load data_schema model for data_schema_type={values["data_schema_type"]}'
                )
        return schema

    @validator("dataset_id")
    def check_dataset_id(cls, dataset_id):
        """Check dataset ID validity"""
        check_config_id_strict(dataset_id, "Dataset")
        return dataset_id

    @validator("trivial_dimensions")
    def check_time_not_trivial(cls, trivial_dimensions):
        for dim in trivial_dimensions:
            if dim == DimensionType.TIME:
                raise ValueError(
                    "The time dimension is currently not a dsgrid supported trivial dimension."
                )
        return trivial_dimensions

    @validator("dimensions")
    def check_files(cls, values: list) -> list:
        """Validate dimension files are unique across all dimensions"""
        check_uniqueness(
            (x.filename for x in values if isinstance(x, DimensionModel)),
            "dimension record filename",
        )
        return values

    @validator("dimensions")
    def check_names(cls, values: list) -> list:
        """Validate dimension names are unique across all dimensions."""
        check_uniqueness(
            [dim.name for dim in values],
            "dimension record name",
        )
        return values

    @validator("dimensions", pre=True, each_item=True, always=True)
    def handle_dimension_union(cls, values):
        return handle_dimension_union(values)

    @validator("dimensions")
    def check_time_zone(cls, dimensions: list, values: dict) -> list:
        """Validate whether required time zone information is present."""
        geo_requires_time_zone = False
        time_dim = None
        if not values["use_project_geography_time_zone"]:
            for dimension in dimensions:
                if dimension.dimension_type == DimensionType.TIME:
                    geo_requires_time_zone = dimension.is_time_zone_required_in_geography()
                    time_dim = dimension
                    break

        if geo_requires_time_zone:
            for dimension in dimensions:
                if dimension.dimension_type == DimensionType.GEOGRAPHY:
                    check_timezone_in_geography(
                        dimension,
                        err_msg=f"Dataset with time dimension {time_dim} requires that its "
                        "geography dimension records include a time_zone column.",
                    )

        return dimensions


class DatasetConfig(ConfigBase):
    """Provides an interface to a DatasetConfigModel."""

    def __init__(self, model):
        super().__init__(model)
        self._dimensions = {}  # ConfigKey to DatasetConfig
        self._dataset_path = None

    @staticmethod
    def config_filename():
        return "dataset.json5"

    @property
    def config_id(self):
        return self._model.dataset_id

    @staticmethod
    def model_class():
        return DatasetConfigModel

    @classmethod
    def load_from_registry(cls, model, registry_data_path):
        # Join with forward slashes instead of Path because this might be an s3 path and
        # we don't want backslashes on Windows.
        config = cls(model)
        config.dataset_path = (
            f"{registry_data_path}/data/{model.dataset_id}/{model.dataset_version}"
        )
        return config

    @classmethod
    def load_from_user_path(cls, config_file, dataset_path):
        config = cls.load(config_file)
        schema_type = config.model.data_schema_type
        if str(dataset_path).startswith("s3://"):
            # TODO: This may need to handle AWS s3 at some point.
            raise DSGInvalidParameter("Registering a dataset from an S3 path is not supported.")
        if not dataset_path.exists():
            raise DSGInvalidParameter(f"Dataset {dataset_path} does not exist")
        dataset_path = str(dataset_path)
        if schema_type == DataSchemaType.STANDARD:
            check_load_data_filename(dataset_path)
            check_load_data_lookup_filename(dataset_path)
        elif schema_type == DataSchemaType.ONE_TABLE:
            check_load_data_filename(dataset_path)
        else:
            raise DSGInvalidParameter(f"data_schema_type={schema_type} not supported.")

        config.dataset_path = dataset_path
        return config

    @property
    def dataset_path(self):
        """Return the directory containing the dataset file(s)."""
        return self._dataset_path

    @dataset_path.setter
    def dataset_path(self, dataset_path):
        """Set the dataset path."""
        self._dataset_path = dataset_path

    @property
    def load_data_path(self):
        return check_load_data_filename(self._dataset_path)

    @property
    def load_data_lookup_path(self):
        return check_load_data_lookup_filename(self._dataset_path)

    def update_dimensions(self, dimensions):
        """Update all dataset dimensions."""
        self._dimensions.update(dimensions)

    @property
    def dimensions(self):
        return self._dimensions

    def get_dimension(self, dimension_type: DimensionType):
        """Return the dimension matching dimension_type.

        Parameters
        ----------
        dimension_type : DimensionType

        Returns
        -------
        DimensionConfig

        """
        for dim_config in self.dimensions.values():
            if dim_config.model.dimension_type == dimension_type:
                return dim_config
        assert False, dimension_type

    def add_trivial_dimensions(self, df):
        """Add trivial 1-element dimensions to load_data_lookup."""
        for dim in self._dimensions.values():
            if dim.model.dimension_type in self.model.trivial_dimensions:
                self._check_trivial_record_length(dim.model.records)
                val = dim.model.records[0].id
                col = dim.model.dimension_type.value
                df = df.withColumn(col, F.lit(val))
        return df

    def remove_trivial_dimensions(self, df):
        trivial_cols = {d.value for d in self.model.trivial_dimensions}
        select_cols = [col for col in df.columns if col not in trivial_cols]
        return df[select_cols]

    def _check_trivial_record_length(self, records):
        """Check that trivial dimensions have only 1 record."""
        if len(records) > 1:
            raise DSGInvalidDimension(
                f"Trivial dimensions must have only 1 record but {len(records)} records found for dimension: {records}"
            )
