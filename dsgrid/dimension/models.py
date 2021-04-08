"""Defines dimension models"""

import csv
import importlib
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

from pydantic.class_validators import root_validator, validator
from pydantic.dataclasses import dataclass
from pydantic.fields import Field
from semver import VersionInfo

from dsgrid.common import LOCAL_REGISTRY
from dsgrid.dimension.base import DimensionType
from dsgrid.dimension.time import (
    LeapDayAdjustmentType,
    Period,
    TimeValueMeasurement,
    TimeFrequency,
    TimezoneType,
)
from dsgrid.exceptions import DSGInvalidDimension
from dsgrid.models import DSGBaseModel
from dsgrid.registry.common import make_version
from dsgrid.utils.aws import sync
from dsgrid.utils.files import load_data


# TODO: BaseDimensionRecordModel? This name is too close to DimensionBaseModel.


class DSGBaseDimensionModel(DSGBaseModel):
    """Base class for all dsgrid dimension models"""

    id: str = Field(
        title="ID",
        description="unique identifier within a dimension",
    )
    name: str = Field(
        title="name",
        description="user-defined name",
    )


class EndUseDimensionModel(DSGBaseDimensionModel):
    """Base class for all end use dimensions"""


class GeographicDimensionModel(DSGBaseDimensionModel):
    """Base class for all geography dimensions"""


class ModelDimensionModel(DSGBaseDimensionModel):
    """Base class for all load model dimensions"""


class ModelYearDimensionModel(DSGBaseDimensionModel):
    """Base class for all model year dimensions"""


class ScenarioDimensionModel(DSGBaseDimensionModel):
    """Base class for all scenario dimensions"""


class SectorDimensionModel(DSGBaseDimensionModel):
    """Base class for all subsector dimensions"""


class SubSectorDimensionModel(DSGBaseDimensionModel):
    """Base class for all subsector dimensions"""


class BaseTimeDimensionModel(DSGBaseDimensionModel):
    """Base class for all time dimensions"""


class WeatherDimensionModel(DSGBaseDimensionModel):
    """Base class for weather dimensions"""


_DIMENSION_TO_MODEL = {
    DimensionType.END_USE: EndUseDimensionModel,
    DimensionType.GEOGRAPHY: GeographicDimensionModel,
    DimensionType.SECTOR: SectorDimensionModel,
    DimensionType.SUBSECTOR: SubSectorDimensionModel,
    DimensionType.TIME: BaseTimeDimensionModel,
    DimensionType.WEATHER: WeatherDimensionModel,
    DimensionType.MODEL_YEAR: ModelYearDimensionModel,
    DimensionType.SCENARIO: ScenarioDimensionModel,
    DimensionType.MODEL: ModelDimensionModel,
}


def get_dimension_model(type_enum):
    """Return the dimension model class for a DimensionType."""
    dim_model = _DIMENSION_TO_MODEL.get(type_enum)
    if dim_model is None:
        raise DSGInvalidDimension(f"no mapping for {type_enum}")
    return dim_model


class DimensionBaseModel(DSGBaseModel):
    """Common attributes for all dimensions"""

    name: str = Field(
        title="name",
        description="dimension name",
    )
    dimension_type: DimensionType = Field(
        title="dimension_type",
        alias="type",
        description="type of the dimension",
    )
    module: Optional[str] = Field(
        title="module",
        description="dimension module",
        default="dsgrid.dimension.standard",
    )
    class_name: Optional[str] = Field(
        title="class_name",
        description="dimension model class name",
        alias="class",
    )
    cls: Optional[type] = Field(
        title="cls",
        description="dimension model class",
        alias="dimension_class",
    )
    description: Optional[str] = Field(
        title="description of dimension record",
        description="description of dimension record, this gets stored in both dimension config file and dimension registry",
        alias="description",
    )

    @validator("name")
    def check_name(cls, name):
        if name == "":
            raise ValueError(f'Empty name field for dimension: "{cls}"')
        return name

    @validator("class_name", always=True)
    def get_dimension_class_name(cls, class_name, values):
        """Set class_name based on inputs."""
        if "name" not in values:
            # An error occurred with name. Ignore everything else.
            return class_name

        mod = importlib.import_module(values["module"])
        cls_name = class_name or values["name"]
        if not hasattr(mod, cls_name):
            if class_name is None:
                msg = (
                    "Setting class based on name failed. "
                    f'There is no class "{cls_name}" in module: {mod}.'
                    "\nIf you are using a unique dimension name, you must "
                    "specify the dimension class."
                )
            else:
                msg = f"dimension class {class_name} not in {mod}"
            raise ValueError(msg)

        return cls_name

    @validator("cls", always=True)
    def get_dimension_class(cls, dim_class, values):
        if "name" not in values or values.get("class_name") is None:
            # An error occurred with name. Ignore everything else.
            return None

        if dim_class is not None:
            raise ValueError(f"cls={dim_class} should not be set")

        return getattr(
            importlib.import_module(values["module"]),
            values["class_name"],
        )


class DimensionModel(DimensionBaseModel):
    """Defines a non-time dimension"""

    filename: str = Field(
        title="filename",
        alias="file",
        description="filename containing dimension records",
    )
    dimension_id: Optional[str] = Field(
        title="dimension_id",
        alias="id",
        description="unique identifier, generated by dsgrid",
    )
    # TODO: I think we may remove mappings altogether in favor of associations
    # TODO: I think we need to add the association table to
    #   dimensions.associations.project_dimensions in the config
    association_table: Optional[str] = Field(
        title="association_table",
        description="optional table that provides mappings of foreign keys",
    )
    # TODO: some of the dimensions will enforce dimension mappings while
    #   others may not
    # TODO: I really don't think we need these mappings at this stage.
    #   I think association tables are fine.
    # one_to_many_mappings: Optional[List[OneToManyMapping]] = Field(
    #     title="one_to_many_mappings",
    #     description="Defines one-to-many mappings for this dimension",
    #     default=[],
    # )
    # many_to_one_mappings: Optional[List[ManyToOneMapping]] = Field(
    #     title="many_to_one_mappings",
    #     description="Defines many-to-one mappings for this dimension",
    #     default=[],
    # )
    # many_to_many_mappings: Optional[List[ManyToManyMapping]] = Field(
    #     title="many_to_many_mappings",
    #     description="Defines many-to-many mappings for this dimension",
    #     default=[],
    # )
    records: Optional[List[Dict]] = Field(
        title="records",
        description="dimension records in filename that get loaded at runtime",
        default=[],
    )

    @validator("filename")
    def check_file(cls, filename):
        """Validate that dimension file exists and has no errors"""
        # validation for S3 paths (sync locally)
        if filename.startswith("s3://"):
            path = LOCAL_REGISTRY / filename.replace("s3://", "")
            breakpoint()  # TODO DT: this doesn't look right for AWS
            # sync(filename, path)

        # Validate that filename exists
        if not os.path.isfile(filename):
            raise ValueError(f"file {filename} does not exist")

        return filename

    # TODO: is this what we want?
    # @validator(
    #     "one_to_many_mappings",
    #     "many_to_one_mappings",
    #     "many_to_many_mappings",
    #     each_item=True,
    # )
    # def add_mapping_dimension_types(cls, val, values):
    #     """Find the dimension mappings types and add them."""
    #     module = importlib.import_module(values["module"])
    #     val.from_dimension_cls = getattr(module, val.from_dimension, None)
    #     if val.from_dimension_cls is None:
    #         raise ValueError(f"{module} does not define {val.from_dimension}")

    #     val.to_dimension_cls = getattr(module, val.to_dimension, None)
    #     if val.to_dimension_cls is None:
    #         raise ValueError(f"{module} does not define {val.to_dimension}")

    #     return val

    @validator("records", always=True)
    def add_records(cls, records, values):
        """Add records from the file."""
        prereqs = ("name", "filename", "cls")
        for req in prereqs:
            if values.get(req) is None:
                return records

        filename = values["filename"]
        dim_class = values["cls"]
        assert not filename.startswith("s3://")  # TODO: see above

        if records:
            raise ValueError("records should not be defined in the project config")

        # Deserialize the model, validate, add default values, ensure id
        # uniqueness, and then store as dict that can be loaded into
        # Spark later.
        # TODO: Do we want to make sure name is unique too?
        ids = set()
        # TODO: Dan - we want to add better support for csv
        if values["filename"].endswith(".csv"):
            filerecords = csv.DictReader(open(values["filename"]))
        else:
            filerecords = load_data(values["filename"])
        for record in filerecords:
            actual = dim_class(**record)
            if actual.id in ids:
                raise ValueError(
                    f"{actual.id} is stored multiple times for " f"{dim_class.__name__}"
                )
            ids.add(actual.id)
            records.append(actual.dict())

        return records


class TimeDimensionModel(DimensionBaseModel):
    """Defines a time dimension"""

    # TODO: what is this intended purpose?
    #       originally i thought it was to interpret start/end, but
    #       the year here is unimportant because it will be based on
    #       the weather_year
    str_format: Optional[str] = Field(
        title="str_format",
        default="%Y-%m-%d %H:%M:%s-%z",
        description="timestamp format",
    )
    # TODO: we may want a list of start and end times;
    #       can this be string or list of strings?
    start: datetime = Field(
        title="start",
        description="first timestamp in the data",
    )
    # TODO: Is this inclusive or exclusive? --> mm:I don't know what this means
    # TODO: we may want to support a list of start and end times
    end: datetime = Field(
        title="end",
        description="last timestamp in the data",
    )
    # TODO: it would be nice to have this be a func that splits nmbr from unit
    frequency: TimeFrequency = Field(
        title="frequency",
        description="resolution of the timestamps",
    )
    includes_dst: bool = Field(
        title="includes_dst",
        description="includes daylight savings time",
    )
    leap_day_adjustment: Optional[LeapDayAdjustmentType] = Field(
        title="leap_day_adjustment",
        default=None,
        description="TODO",
    )
    period: Period = Field(
        title="period",
        description="TODO",
    )
    timezone: TimezoneType = Field(
        title="timezone",
        description="timezone of data",
    )
    # TODO: is this a project-level time dimension config?
    value_representation: TimeValueMeasurement = Field(
        title="value_representation",
        default="mean",
        description="TODO",
    )

    @validator("start", "end", pre=True)
    def check_times(cls, val, values):
        # TODO: technical year doesn't matter; needs to apply the weather year
        # make sure start and end time parse
        datetime.strptime(val, values["str_format"])
        # TODO: validate consistency between start, end, frequency
        return val


class DimensionReferenceModel(DSGBaseModel):
    """Reference to a dimension stored in the registry"""

    dimension_type: DimensionType = Field(
        title="dimension_type",
        alias="type",
        description="type of the dimension",
    )
    dimension_id: str = Field(
        title="dimension_id",
        description="unique ID of the dimension",
    )
    version: Union[str, VersionInfo] = Field(
        title="version",
        description="version of the dimension",
    )

    @validator("version")
    def check_version(cls, version):
        if isinstance(version, str):
            return make_version(version)
        return version


DimensionUnionModel = List[Union[DimensionModel, DimensionReferenceModel, TimeDimensionModel]]


def handle_dimension_union(value):
    """
    Validate dimension type work around for pydantic Union bug
    related to: https://github.com/samuelcolvin/pydantic/issues/619
    """
    # NOTE: Errors inside DimensionModel or TimeDimensionModel will be duplicated by Pydantic
    if value["type"] == DimensionType.TIME.value:
        val = TimeDimensionModel(**value)
    elif sorted(value.keys()) == ["dimension_id", "type", "version"]:
        val = DimensionReferenceModel(**value)
    else:
        val = DimensionModel(**value)
    return val
