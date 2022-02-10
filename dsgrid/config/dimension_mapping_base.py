import logging
import os
from typing import Dict, List, Optional, Union

from pydantic import Field, validator
from semver import VersionInfo

from .dimensions import DimensionReferenceModel
from dsgrid.data_models import DSGBaseModel
from dsgrid.dimension.base_models import DimensionType
from dsgrid.utils.versioning import handle_version_or_str


logger = logging.getLogger(__name__)


class DimensionMappingBaseModel(DSGBaseModel):
    """Base class for mapping dimensions"""

    from_dimension: DimensionReferenceModel = Field(
        title="from_dimension",
        description="From dimension",
    )
    to_dimension: DimensionReferenceModel = Field(
        title="to_dimension",
        description="To dimension",
    )
    description: str = Field(
        title="description",
        description="Description of dimension mapping",
    )
    mapping_id: Optional[str] = Field(
        title="mapping_id",
        alias="id",
        description="Unique dimension mapping identifier, generated by dsgrid",
        dsg_internal=True,
        updateable=False,
    )


class DimensionMappingReferenceModel(DSGBaseModel):
    """Reference to a dimension mapping stored in the registry.

    The DimensionMappingReferenceModel is utilized by the project configuration (project.toml) as well as by the dimension mapping reference configuration (dimension_mapping_references.toml) that may be required when submitting a dataset to a project.
    """

    from_dimension_type: DimensionType = Field(
        title="from_dimension_type",
        description="Dimension Type",
        options=DimensionType.format_for_docs(),
    )
    to_dimension_type: DimensionType = Field(
        title="to_dimension_type",
        description="Dimension Type",
        options=DimensionType.format_for_docs(),
    )
    mapping_id: str = Field(
        title="mapping_id",
        description="Unique ID of the dimension mapping",
        updateable=False,
    )
    version: Union[str, VersionInfo] = Field(
        title="version",
        description="Version of the dimension",
        # TODO: add notes about warnings for outdated versions DSGRID-189 & DSGRID-148
    )
    required_for_validation: Optional[bool] = Field(
        title="version",
        description="Set to False if a given base-to-base dimension mapping is NOT required for input dataset validation; default is True",
        default=True,
        # TODO: add notes about warnings for outdated versions DSGRID-189 & DSGRID-148
    )

    @validator("version")
    def check_version(cls, version):
        return handle_version_or_str(version)

    # @validator("required_for_validation")
    # def check_required_for_validation_field(cls, value):
    #     # TODO if base_to_supplemental, raise error
    #     return value


class DimensionMappingReferenceListModel(DSGBaseModel):
    """List of dimension mapping references used by the dimensions_mappings.toml config"""

    references: List[DimensionMappingReferenceModel] = Field(
        title="references",
        description="List of dimension mapping references",
    )
