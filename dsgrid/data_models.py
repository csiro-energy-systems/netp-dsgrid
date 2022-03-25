"""Base functionality for all Pydantic data models used in dsgrid"""

from enum import Enum
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ValidationError
from pydantic.json import isoformat, timedelta_isoformat
from semver import VersionInfo

from dsgrid.utils.files import load_data


logger = logging.getLogger(__name__)


class DSGBaseModel(BaseModel):
    """Base data model for all dsgrid data models"""

    class Config:
        title = "DSGBaseModel"
        anystr_strip_whitespace = True
        validate_assignment = True
        validate_all = True
        extra = "forbid"
        use_enum_values = False
        arbitrary_types_allowed = True
        allow_population_by_field_name = True

    @classmethod
    def load(cls, filename, data=None):
        """Load a data model from a file.
        Temporarily changes to the file's parent directory so that Pydantic
        validators can load relative file paths within the file.

        Parameters
        ----------
        filename : str
        data : None, dict
            If not None, use this dictionary instead of loading from the file.
            This is for situations where the contents of the file were modified but Pydantic
            validation still requires handling of relative file paths.

        """
        filename = Path(filename)
        base_dir = filename.parent.absolute()
        orig = os.getcwd()
        os.chdir(base_dir)
        try:
            if data is None:
                cfg = cls(**load_data(filename.name))
            else:
                cfg = cls(**data)
            return cfg
        except ValidationError:
            logger.exception("Failed to validate %s", filename)
            raise
        finally:
            os.chdir(orig)

    @classmethod
    def schema_json(cls, by_alias=True, indent=None) -> str:
        data = cls.schema(by_alias=by_alias)
        return json.dumps(data, indent=indent, cls=ExtendedJSONEncoder)

    @classmethod
    def get_fields_with_extra_attribute(cls, attribute):
        fields = set()
        for f, attrs in cls.__fields__.items():
            if attrs.field_info.extra.get(attribute):
                fields.add(f)
        return fields


class EnumValue:
    """Class to define a DSGEnum value"""

    def __init__(self, value, description, **kwargs):
        self.value = value
        self.description = description
        for kwarg, val in kwargs.items():
            self.__setattr__(kwarg, val)


class DSGEnum(Enum):
    """dsgrid Enum class"""

    def __new__(cls, *args):
        obj = object.__new__(cls)
        assert len(args) in (1, 2)
        if isinstance(args[0], EnumValue):
            obj._value_ = args[0].value
            obj.description = args[0].description
            for attr, val in args[0].__dict__.items():
                if attr not in ("value", "description"):
                    setattr(obj, attr, val)
        elif len(args) == 2:
            obj._value_ = args[0]
            obj.description = args[1]
        else:
            obj._value_ = args[0]
            obj.description = None
        return obj

    @classmethod
    def format_for_docs(cls):
        """Returns set of formatted enum values for docs."""
        return str([e.value for e in cls]).replace("'", "``")

    @classmethod
    def format_descriptions_for_docs(cls):
        """Returns formatted dict of enum values and descriptions for docs."""
        desc = {}
        for e in cls:
            desc[f"``{e.value}``"] = f"{e.description}"
        return desc


class ExtendedJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, VersionInfo):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return isoformat(obj)
        if isinstance(obj, timedelta):
            return timedelta_isoformat(obj)

        return json.JSONEncoder.default(self, obj)


def serialize_model(model: DSGBaseModel, by_alias=True, exclude=None):
    """Serialize a model to a dict, converting values as needed.

    Parameters
    ----------
    by_alias : bool
        Forwarded to pydantic.BaseModel.dict.
    exclude : set
        Forwarded to pydantic.BaseModel.dict.

    """
    # TODO: we should be able to use model.json and custom JSON encoders
    # instead of doing this, at least in most cases.
    return serialize_model_data(model.dict(by_alias=by_alias, exclude=exclude))


def serialize_user_model(model: DSGBaseModel):
    """Serialize the user model to a dict, converting values as needed and ignoring fields generated by dsgrid."""
    # TODO: we should be able to use model.json and custom JSON encoders
    # instead of doing this, at least in most cases.
    exclude = type(model).get_fields_with_extra_attribute("dsg_internal")
    return serialize_model_data(model.dict(by_alias=True, exclude=exclude))


def serialize_model_data(data: dict):
    for key, val in data.items():
        data[key] = _serialize_model_item(val)
    return data


def _serialize_model_item(val):
    if isinstance(val, Enum):
        return val.value
    if isinstance(val, VersionInfo):
        return str(val)
    if isinstance(val, datetime):
        return isoformat(val)
    if isinstance(val, timedelta):
        return timedelta_isoformat(val)
    if isinstance(val, dict):
        return serialize_model_data(val)
    if isinstance(val, list):
        return [_serialize_model_item(x) for x in val]
    return val
