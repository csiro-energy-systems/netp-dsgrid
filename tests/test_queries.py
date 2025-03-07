import abc
import logging
import math
import shutil
import tempfile
from collections import defaultdict, namedtuple
from pathlib import Path

import pyspark.sql.functions as F
from pyspark.sql.types import DoubleType, IntegerType, StringType, StructField, StructType
import pytest
from click.testing import CliRunner
from pyspark.sql import SparkSession

from dsgrid.cli.dsgrid import cli
from dsgrid.dimension.base_models import DimensionType
from dsgrid.config.mapping_tables import MappingTableRecordModel
from dsgrid.dimension.dimension_filters import (
    DimensionFilterExpressionModel,
    DimensionFilterColumnOperatorModel,
    SupplementalDimensionFilterColumnOperatorModel,
)
from dsgrid.exceptions import DSGInvalidQuery
from dsgrid.loggers import setup_logging
from dsgrid.project import Project
from dsgrid.query.models import (
    AggregationModel,
    ColumnModel,
    ColumnType,
    CompositeDatasetQueryModel,
    CreateCompositeDatasetQueryModel,
    DatasetModel,
    DimensionQueryNamesModel,
    ProjectQueryDatasetParamsModel,
    ProjectQueryParamsModel,
    ProjectQueryModel,
    QueryResultParamsModel,
    ReportInputModel,
    ReportType,
    ExponentialGrowthDatasetModel,
    StandaloneDatasetModel,
)
from dsgrid.query.query_submitter import ProjectQuerySubmitter, CompositeDatasetQuerySubmitter
from dsgrid.query.report_peak_load import PeakLoadInputModel, PeakLoadReport
from dsgrid.registry.registry_database import DatabaseConnection, RegistryDatabase
from dsgrid.registry.registry_manager import RegistryManager
from dsgrid.utils.spark import models_to_dataframe
from dsgrid.utils.utilities import convert_record_dicts_to_classes


DIMENSION_MAPPING_SCHEMA = StructType(
    [
        StructField("from_id", StringType(), False),
        StructField("to_id", StringType()),
        StructField("from_fraction", DoubleType()),
    ]
)
REGISTRY_PATH = Path("dsgrid-test-data/filtered_registries/simple_standard_scenarios")

Datasets = namedtuple("Datasets", ["comstock", "resstock", "tempo"])

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def la_expected_electricity_hour_16(tmp_path_factory):
    output_dir = tmp_path_factory.mktemp("diurnal_queries")
    project = get_project("simple-standard-scenarios", "dsgrid_conus_2022")
    query = ProjectQueryModel(
        name="projected_dg_conus_2022",
        project=ProjectQueryParamsModel(
            project_id="dsgrid_conus_2022",
            include_dsgrid_dataset_components=False,
            dataset=DatasetModel(
                dataset_id="projected_dg_conus_2022",
                source_datasets=[
                    StandaloneDatasetModel(dataset_id="comstock_conus_2022_projected"),
                    StandaloneDatasetModel(dataset_id="resstock_conus_2022_projected"),
                ],
            ),
        ),
    )
    ProjectQuerySubmitter(project, output_dir).submit(
        query,
        persist_intermediate_table=False,
        load_cached_table=False,
    )
    df = read_parquet(str(output_dir / query.name / "table.parquet")).filter("county == '06037'")
    df = df.withColumn("elec", df.electricity_cooling + df.electricity_heating).drop(
        "electricity_cooling", "electricity_heating"
    )
    expected = (
        df.groupBy("county", F.hour("time_est").alias("hour"))
        .agg(F.mean("elec"))
        .filter("hour == 16")
        .collect()[0]["avg(elec)"]
    )
    yield {
        "la_electricity_hour_16": expected,
    }


def test_electricity_values():
    run_query_test(QueryTestElectricityValues, True)
    run_query_test(QueryTestElectricityValues, False)


def test_electricity_use_by_county():
    run_query_test(QueryTestElectricityUse, "county", "sum")
    run_query_test(QueryTestElectricityUse, "county", "max")


def test_electricity_use_by_state():
    run_query_test(QueryTestElectricityUse, "state", "sum")
    run_query_test(QueryTestElectricityUse, "state", "max")


def test_electricity_use_with_results_filter():
    run_query_test(QueryTestElectricityUseFilterResults, "county", "sum")


def test_total_electricity_use_with_filter():
    run_query_test(QueryTestTotalElectricityUseWithFilter)


@pytest.mark.parametrize(
    "column_inputs",
    [
        (ColumnType.DIMENSION_QUERY_NAMES, ["state", "reeds_pca", "census_region"], True),
        (ColumnType.DIMENSION_TYPES, ["reeds_pca"], True),
        (ColumnType.DIMENSION_TYPES, ["state"], True),
        (ColumnType.DIMENSION_TYPES, ["state", "reeds_pca", "census_region"], False),
    ],
)
def test_total_electricity_use_by_state_and_pca(column_inputs):
    column_type, columns, is_valid = column_inputs
    if is_valid:
        run_query_test(QueryTestElectricityUseByStateAndPCA, column_type, columns)
    else:
        with pytest.raises(ValueError):
            run_query_test(QueryTestElectricityUseByStateAndPCA, column_type, columns)


def test_diurnal_electricity_use_by_county_chained(la_expected_electricity_hour_16):
    run_query_test(
        QueryTestDiurnalElectricityUseByCountyChained,
        expected_values=la_expected_electricity_hour_16,
    )


def test_peak_load():
    run_query_test(QueryTestPeakLoadByStateSubsector)


def test_map_annual_time():
    run_query_test(QueryTestMapAnnualTime)


def test_invalid_drop_pivoted_dimension(tmp_path):
    invalid_agg = AggregationModel(
        dimensions=DimensionQueryNamesModel(
            geography=["county"],
            metric=[],
            model_year=["model_year"],
            scenario=["scenario"],
            sector=["sector"],
            subsector=["subsector"],
            time=["time_est"],
            weather_year=["weather_2012"],
        ),
        aggregation_function="sum",
    )
    query = ProjectQueryModel(
        name="test",
        project=ProjectQueryParamsModel(
            project_id="dsgrid_conus_2022",
            include_dsgrid_dataset_components=False,
            dataset=DatasetModel(
                dataset_id="projected_dg_conus_2022",
                source_datasets=[
                    StandaloneDatasetModel(
                        dataset_id="comstock_conus_2022_reference",
                    ),
                    StandaloneDatasetModel(
                        dataset_id="resstock_conus_2022_reference",
                    ),
                ],
            ),
        ),
        result=QueryResultParamsModel(
            output_format="parquet",
        ),
    )
    project = get_project("simple-standard-scenarios", "dsgrid_conus_2022")
    output_dir = tmp_path / "queries"

    query.result.aggregations = [invalid_agg]
    with pytest.raises(DSGInvalidQuery):
        ProjectQuerySubmitter(project, output_dir).submit(query)


def test_create_composite_dataset_query(tmp_path):
    output_dir = tmp_path / "queries"
    project = get_project("simple-standard-scenarios", "dsgrid_conus_2022")
    query = QueryTestElectricityValuesCompositeDataset(
        REGISTRY_PATH, project, output_dir=output_dir
    )
    CompositeDatasetQuerySubmitter(project, output_dir).create_dataset(query.make_query())
    query.validate()

    query2 = QueryTestElectricityValuesCompositeDatasetAgg(
        REGISTRY_PATH, project, output_dir=output_dir, geography="county"
    )
    CompositeDatasetQuerySubmitter(project, output_dir).submit(query2.make_query())

    query3 = QueryTestElectricityValuesCompositeDatasetAgg(
        REGISTRY_PATH,
        project,
        output_dir=output_dir,
        geography="state",
    )
    CompositeDatasetQuerySubmitter(project, output_dir).submit(query3.make_query())


def test_query_cli_create_validate(tmp_path):
    filename = tmp_path / "query.json5"
    cmd = [
        "--offline",
        "--database-name",
        "simple-standard-scenarios",
        "query",
        "project",
        "create",
        "-d",
        "-r",
        "-f",
        str(filename),
        "-F",
        "expression",
        "-F",
        "column_operator",
        "-F",
        "supplemental_column_operator",
        "-F",
        "raw",
        "--force",
        "my_query",
        "dsgrid_conus_2022",
        "projected_dg_conus_2022",
    ]
    shutdown_project()
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, cmd)
    assert result.exit_code == 0
    query = ProjectQueryModel.from_file(filename)
    assert query.name == "my_query"
    assert query.result.aggregations
    result = runner.invoke(cli, ["query", "project", "validate", str(filename)])
    assert result.exit_code == 0


def test_query_cli_run(tmp_path):
    output_dir = tmp_path / "queries"
    project = get_project(
        QueryTestElectricityValues.get_database_name(), QueryTestElectricityValues.get_project_id()
    )
    query = QueryTestElectricityValues(True, REGISTRY_PATH, project, output_dir=output_dir)
    filename = tmp_path / "query.json"
    filename.write_text(query.make_query().json(indent=2))
    cmd = [
        "--offline",
        "--database-name",
        "simple-standard-scenarios",
        "query",
        "project",
        "run",
        "--output",
        str(output_dir),
        str(filename),
    ]
    shutdown_project()
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(cli, cmd)
    assert result.exit_code == 0
    query.validate()


def test_dimension_query_names_model():
    # Test that this model is defined with all dimension types.
    diff = {x.value for x in DimensionType}.symmetric_difference(
        set(DimensionQueryNamesModel.__fields__)
    )
    assert not diff


def test_unit_mapping(cached_registry):
    run_query_test(QueryTestUnitMapping)


_projects = {}


def get_project(database, project_id):
    """Load a Project and cache it for future calls.
    Loading is slow and the Project isn't being changed by these tests.
    """
    key = (database, project_id)
    if key in _projects:
        return _projects[key]
    conn = DatabaseConnection(database=database)
    mgr = RegistryManager.load(
        conn,
        offline_mode=True,
    )
    _projects[key] = mgr.project_manager.load_project(project_id)
    return _projects[key]


def shutdown_project():
    """Shutdown a project and stop the SparkSession so that another process can create one."""
    _projects.clear()
    spark = SparkSession.getActiveSession()
    if spark is not None:
        spark.stop()


def run_query_test(test_query_cls, *args, expected_values=None):
    output_dir = Path(tempfile.gettempdir()) / "queries"
    if output_dir.exists():
        shutil.rmtree(output_dir)

    project = get_project(test_query_cls.get_database_name(), test_query_cls.get_project_id())
    try:
        query = test_query_cls(*args, REGISTRY_PATH, project, output_dir=output_dir)
        for load_cached_table in (False, True):
            ProjectQuerySubmitter(project, output_dir).submit(
                query.make_query(),
                persist_intermediate_table=True,
                load_cached_table=load_cached_table,
                force=True,
            )
            query.validate(expected_values=expected_values)
    finally:
        if output_dir.exists():
            shutil.rmtree(output_dir)


class QueryTestBase(abc.ABC):
    """Base class for all test queries"""

    stats = None

    def __init__(self, registry_path, project, output_dir=Path("queries")):
        self._registry_path = Path(registry_path)
        self._project = project
        self._output_dir = Path(output_dir)
        self._model = None

    @staticmethod
    def get_database_name():
        return "simple-standard-scenarios"

    @staticmethod
    def get_project_id():
        return "dsgrid_conus_2022"

    @property
    def name(self):
        """Return the name of the query.

        Returns
        -------
        str

        """
        return self.make_query().name

    @property
    def output_dir(self):
        """Return the output directory for the query results.

        Returns
        -------
        Path

        """
        return self._output_dir

    def get_raw_stats(self):
        """Return the raw stats for the data tables.

        These stats assume that the query model years are ["2018", "2040"].

        Returns
        -------
        dict
        """
        if QueryTestBase.stats is None:
            logger.info("Generate raw stats")
            QueryTestBase.stats = generate_raw_stats(self._registry_path)
        return QueryTestBase.stats

    @abc.abstractmethod
    def make_query(self):
        """Return the query model"""

    @abc.abstractmethod
    def validate(self, expected_values=None):
        """Validate the results

        Parameters
        ----------
        expected_values : dict | None
            Optional dictionary containing expected values from a pytest fixture.

        Returns
        -------
        bool
            Return True when the validation is successful.

        """

    def get_filtered_county_id(self):
        filters = self._model.project.dataset_params.dimension_filters
        counties = [x.value for x in filters if x.dimension_query_name == "county"]
        assert len(counties) == 1, f"Unexpected length of filtered counties: {len(counties)}"
        return counties[0]


class QueryTestElectricityValues(QueryTestBase):

    NAME = "electricity-values"

    def __init__(self, use_supplemental_dimension, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._use_supplemental_dimension = use_supplemental_dimension

    def make_query(self):
        self._model = ProjectQueryModel(
            name=self.NAME,
            project=ProjectQueryParamsModel(
                project_id=self.get_project_id(),
                include_dsgrid_dataset_components=False,
                dataset=DatasetModel(
                    dataset_id="projected_dg_conus_2022",
                    source_datasets=[
                        ExponentialGrowthDatasetModel(
                            dataset_id="comstock_conus_2022_projected",
                            initial_value_dataset_id="comstock_conus_2022_reference",
                            growth_rate_dataset_id="aeo2021_reference_commercial_energy_use_growth_factors",
                            construction_method="formula123",
                        ),
                        ExponentialGrowthDatasetModel(
                            dataset_id="resstock_conus_2022_projected",
                            initial_value_dataset_id="resstock_conus_2022_reference",
                            growth_rate_dataset_id="aeo2021_reference_residential_energy_use_growth_factors",
                            construction_method="formula123",
                        ),
                        # StandaloneDatasetModel(dataset_id="tempo_conus_2022"),
                    ],
                    expression="comstock_conus_2022_projected | resstock_conus_2022_projected",
                    # expression="comstock_conus_2022_projected | resstock_conus_2022_projected | tempo_conus_2022",
                    params=ProjectQueryDatasetParamsModel(
                        dimension_filters=[
                            # This is a nonsensical way to filter down to county 06037, but
                            # it tests the code with combinations of base and supplemental
                            # dimension filters.
                            DimensionFilterColumnOperatorModel(
                                dimension_type=DimensionType.GEOGRAPHY,
                                dimension_query_name="county",
                                operator="isin",
                                value=["06037", "36047"],
                            ),
                            DimensionFilterExpressionModel(
                                dimension_type=DimensionType.GEOGRAPHY,
                                dimension_query_name="state",
                                operator="==",
                                column="name",
                                value="California",
                            ),
                        ],
                    ),
                ),
            ),
            result=QueryResultParamsModel(
                supplemental_columns=["state"],
                replace_ids_with_names=True,
            ),
        )
        if self._use_supplemental_dimension:
            self._model.project.dataset.params.dimension_filters.append(
                SupplementalDimensionFilterColumnOperatorModel(
                    dimension_type=DimensionType.METRIC,
                    dimension_query_name="electricity",
                )
            )
        else:
            self._model.project.dataset.params.dimension_filters.append(
                DimensionFilterExpressionModel(
                    dimension_type=DimensionType.METRIC,
                    dimension_query_name="end_use",
                    operator="==",
                    column="fuel_id",
                    value="electricity",
                )
            )
        return self._model

    def validate(self, expected_values=None):
        county = "06037"
        county_name = (
            self._project.config.get_dimension_records("county")
            .filter(f"id == {county}")
            .collect()[0]
            .name
        )
        df = read_parquet(str(self.output_dir / self.name / "table.parquet"))
        assert "natural_gas_heating" not in df.columns
        non_value_columns = self._project.config.get_base_dimension_query_names()
        non_value_columns.update({"id", "timestamp"})
        supp_columns = {x.get_column_name() for x in self._model.result.supplemental_columns}
        non_value_columns.update(supp_columns)
        value_columns = sorted((x for x in df.columns if x not in non_value_columns))
        expected = ["electricity_cooling", "electricity_heating"]
        # expected = ["electricity_cooling", "electricity_ev_l1l2", "electricity_heating"]
        success = value_columns == expected
        if not success:
            logger.error("Mismatch in columns: actual=%s expected=%s", value_columns, expected)
        if supp_columns.difference(df.columns):
            logger.error("supplemental_columns=%s are not present in table", supp_columns)
            success = False
        if not df.select("county").distinct().filter(f"county == '{county_name}'").collect():
            logger.error("County name = %s is not present", county_name)
            success = False
        if success:
            total_cooling = df.agg(F.sum("electricity_cooling").alias("sum")).collect()[0].sum
            total_heating = df.agg(F.sum("electricity_heating").alias("sum")).collect()[0].sum
            expected = self.get_raw_stats()["by_county"][county]["comstock_resstock"]["sum"]
            assert math.isclose(total_cooling, expected["electricity_cooling"])
            assert math.isclose(total_heating, expected["electricity_heating"])


class QueryTestElectricityUse(QueryTestBase):

    NAME = "total_electricity_use"

    def __init__(self, geography, op, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._geography = geography
        self._op = op

    def make_query(self):
        self._model = ProjectQueryModel(
            name=self.NAME,
            project=ProjectQueryParamsModel(
                project_id=self.get_project_id(),
                include_dsgrid_dataset_components=False,
                dataset=DatasetModel(
                    dataset_id="projected_dg_conus_2022",
                    source_datasets=[
                        StandaloneDatasetModel(dataset_id="comstock_conus_2022_projected"),
                        StandaloneDatasetModel(dataset_id="resstock_conus_2022_projected"),
                    ],
                ),
            ),
            result=QueryResultParamsModel(
                aggregations=[
                    AggregationModel(
                        dimensions=DimensionQueryNamesModel(
                            geography=[self._geography],
                            metric=["electricity_collapsed"],
                            model_year=[],
                            scenario=[],
                            sector=[],
                            subsector=[],
                            time=[],
                            weather_year=[],
                        ),
                        aggregation_function=self._op,
                    ),
                ],
                output_format="parquet",
            ),
        )
        return self._model

    def validate(self, expected_values=None):
        if self._geography == "county":
            validate_electricity_use_by_county(
                self._op,
                self.output_dir / self.name / "table.parquet",
                self.get_raw_stats(),
                4,
            )
        elif self._geography == "state":
            validate_electricity_use_by_state(
                self._op,
                self.output_dir / self.name / "table.parquet",
                self.get_raw_stats(),
            )
        else:
            assert False, self._geography


class QueryTestElectricityUseFilterResults(QueryTestBase):

    NAME = "total_electricity_use"

    def __init__(self, geography, op, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assert geography in ("county", "state"), geography
        self._geography = geography
        self._op = op

    def make_query(self):
        self._model = ProjectQueryModel(
            name=self.NAME,
            project=ProjectQueryParamsModel(
                project_id=self.get_project_id(),
                include_dsgrid_dataset_components=False,
                dataset=DatasetModel(
                    dataset_id="projected_dg_conus_2022",
                    source_datasets=[
                        StandaloneDatasetModel(dataset_id="comstock_conus_2022_projected"),
                        StandaloneDatasetModel(dataset_id="resstock_conus_2022_projected"),
                    ],
                ),
            ),
            result=QueryResultParamsModel(
                aggregations=[
                    AggregationModel(
                        dimensions=DimensionQueryNamesModel(
                            geography=[self._geography],
                            metric=["electricity_collapsed"],
                            model_year=[],
                            scenario=[],
                            sector=[],
                            subsector=[],
                            time=[],
                            weather_year=[],
                        ),
                        aggregation_function=self._op,
                    ),
                ],
                dimension_filters=[
                    DimensionFilterColumnOperatorModel(
                        dimension_type=DimensionType.GEOGRAPHY,
                        dimension_query_name=self._geography,
                        column=self._geography,
                        operator="isin",
                        value=["06037", "36047"] if self._geography == "county" else ["CA", "NY"],
                    )
                ],
                output_format="parquet",
            ),
        )
        return self._model

    def validate(self, expected_values=None):
        if self._geography == "county":
            validate_electricity_use_by_county(
                self._op,
                self.output_dir / self.name / "table.parquet",
                self.get_raw_stats(),
                2,
            )
        elif self._geography == "state":
            validate_electricity_use_by_state(
                self._op,
                self.output_dir / self.name / "table.parquet",
                self.get_raw_stats(),
            )
        else:
            assert False, self._geography


class QueryTestTotalElectricityUseWithFilter(QueryTestBase):

    NAME = "total_electricity_use"

    def make_query(self):
        self._model = ProjectQueryModel(
            name=self.NAME,
            project=ProjectQueryParamsModel(
                project_id=self.get_project_id(),
                include_dsgrid_dataset_components=False,
                dataset=DatasetModel(
                    dataset_id="projected_dg_conus_2022",
                    source_datasets=[
                        StandaloneDatasetModel(dataset_id="comstock_conus_2022_projected"),
                        StandaloneDatasetModel(dataset_id="resstock_conus_2022_projected"),
                    ],
                ),
            ),
            result=QueryResultParamsModel(
                dimension_filters=[
                    DimensionFilterExpressionModel(
                        dimension_type=DimensionType.GEOGRAPHY,
                        dimension_query_name="county",
                        operator="==",
                        value="06037",
                        column="county",
                    ),
                ],
                aggregations=[
                    AggregationModel(
                        dimensions=DimensionQueryNamesModel(
                            geography=["county"],
                            metric=["electricity_collapsed"],
                            model_year=[],
                            scenario=[],
                            sector=[],
                            subsector=[],
                            time=[],
                            weather_year=[],
                        ),
                        aggregation_function="sum",
                    ),
                ],
                output_format="parquet",
            ),
        )
        return self._model

    def validate(self, expected_values=None):
        validate_electricity_use_by_county(
            "sum",
            self.output_dir / self.name / "table.parquet",
            self.get_raw_stats(),
            1,
        )


class QueryTestDiurnalElectricityUseByCountyChained(QueryTestBase):

    NAME = "diurnal_electricity_use_by_county"

    def make_query(self):
        self._model = ProjectQueryModel(
            name=self.NAME,
            project=ProjectQueryParamsModel(
                project_id=self.get_project_id(),
                include_dsgrid_dataset_components=False,
                dataset=DatasetModel(
                    dataset_id="projected_dg_conus_2022",
                    source_datasets=[
                        StandaloneDatasetModel(dataset_id="comstock_conus_2022_projected"),
                        StandaloneDatasetModel(dataset_id="resstock_conus_2022_projected"),
                    ],
                ),
            ),
            result=QueryResultParamsModel(
                aggregations=[
                    AggregationModel(
                        dimensions=DimensionQueryNamesModel(
                            geography=["county"],
                            metric=["electricity_collapsed"],
                            model_year=["model_year"],
                            scenario=["scenario"],
                            sector=["sector"],
                            subsector=["subsector"],
                            time=["time_est"],
                            weather_year=["weather_2012"],
                        ),
                        aggregation_function="sum",
                    ),
                    AggregationModel(
                        dimensions=DimensionQueryNamesModel(
                            geography=["county"],
                            metric=["electricity_collapsed"],
                            model_year=[],
                            scenario=[],
                            sector=[],
                            subsector=[],
                            time=[
                                ColumnModel(
                                    dimension_query_name="time_est", function="hour", alias="hour"
                                )
                            ],
                            weather_year=[],
                        ),
                        aggregation_function="mean",
                    ),
                ],
                sort_columns=["county", "hour"],
                output_format="parquet",
            ),
        )
        return self._model

    def validate(self, expected_values):
        filename = self.output_dir / self.name / "table.parquet"
        df = read_parquet(str(filename))
        assert not {"all_electricity", "county", "hour"}.difference(df.columns)
        hour = 16
        val = df.filter("county == '06037'").filter(f"hour == {hour}").collect()[0].all_electricity
        assert math.isclose(val, expected_values["la_electricity_hour_16"])


class QueryTestElectricityUseByStateAndPCA(QueryTestBase):

    NAME = "total_electricity_use_by_state_and_pca"

    def __init__(self, column_type: ColumnType, geography_columns, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._column_type = column_type
        self._geography_columns = geography_columns

    def make_query(self):
        self._model = ProjectQueryModel(
            name=self.NAME,
            project=ProjectQueryParamsModel(
                project_id=self.get_project_id(),
                include_dsgrid_dataset_components=False,
                dataset=DatasetModel(
                    dataset_id="projected_dg_conus_2022",
                    source_datasets=[
                        StandaloneDatasetModel(dataset_id="comstock_conus_2022_projected"),
                        StandaloneDatasetModel(dataset_id="resstock_conus_2022_projected"),
                        StandaloneDatasetModel(dataset_id="tempo_conus_2022_mapped"),
                    ],
                ),
            ),
            result=QueryResultParamsModel(
                column_type=self._column_type,
                aggregations=[
                    AggregationModel(
                        dimensions=DimensionQueryNamesModel(
                            geography=self._geography_columns,
                            metric=["electricity_collapsed"],
                            model_year=["model_year"],
                            scenario=["scenario"],
                            sector=["sector"],
                            subsector=[],
                            time=["time_est"],
                            weather_year=["weather_2012"],
                        ),
                        aggregation_function="sum",
                    ),
                ],
                output_format="parquet",
            ),
        )
        return self._model

    def validate(self, expected_values=None):
        df = read_parquet(self.output_dir / self.name / "table.parquet")
        match self._column_type:
            case ColumnType.DIMENSION_QUERY_NAMES:
                assert "time_est" in df.columns
                for column in self._geography_columns:
                    assert column in df.columns
            case ColumnType.DIMENSION_TYPES:
                assert "timestamp" in df.columns
                assert "geography" in df.columns
                for column in self._geography_columns:
                    assert column not in df.columns
            case _:
                assert False, f"Bug: add support for {self._column_type}"


class QueryTestPeakLoadByStateSubsector(QueryTestBase):

    NAME = "peak-load-by-state-subsector"

    def make_query(self):
        self._model = ProjectQueryModel(
            name=self.NAME,
            project=ProjectQueryParamsModel(
                project_id=self.get_project_id(),
                include_dsgrid_dataset_components=False,
                dataset=DatasetModel(
                    dataset_id="projected_dg_conus_2022",
                    source_datasets=[
                        StandaloneDatasetModel(dataset_id="comstock_conus_2022_projected"),
                        StandaloneDatasetModel(dataset_id="resstock_conus_2022_projected"),
                    ],
                ),
            ),
            result=QueryResultParamsModel(
                aggregations=[
                    AggregationModel(
                        dimensions=DimensionQueryNamesModel(
                            geography=["state"],
                            metric=["electricity_collapsed"],
                            model_year=["model_year"],
                            scenario=["scenario"],
                            sector=["sector"],
                            subsector=["subsector"],
                            time=["time_est"],
                            weather_year=["weather_2012"],
                        ),
                        aggregation_function="sum",
                    ),
                ],
                reports=[
                    ReportInputModel(
                        report_type=ReportType.PEAK_LOAD,
                        inputs=PeakLoadInputModel(
                            group_by_columns=["state", "subsector", "scenario", "model_year"]
                        ),
                    ),
                ],
                output_format="parquet",
            ),
        )
        return self._model

    def validate(self, expected_values=None):
        df = read_parquet(self.output_dir / self.name / "table.parquet")
        peak_load = read_parquet(self.output_dir / self.name / PeakLoadReport.REPORT_FILENAME)
        model_year = "2020"
        scenario = "reference"
        state = "CA"
        subsector = "hospital"

        def make_expr(tdf):
            return (
                (tdf.state == state)
                & (tdf.subsector == subsector)
                & (tdf.model_year == model_year)
                & (tdf.scenario == scenario)
            )

        expected = (
            df.filter(make_expr(df))
            .agg(F.max("all_electricity").alias("max_val"))
            .collect()[0]
            .max_val
        )
        actual = peak_load.filter(make_expr(peak_load)).collect()[0].all_electricity
        assert math.isclose(actual, expected)


class QueryTestMapAnnualTime(QueryTestBase):

    NAME = "map-annual-time"

    def make_query(self):
        self._model = ProjectQueryModel(
            name=self.NAME,
            project=ProjectQueryParamsModel(
                project_id=self.get_project_id(),
                include_dsgrid_dataset_components=False,
                dataset=DatasetModel(
                    dataset_id="eia_861_annual_energy_use_state_sector_mapped",
                    source_datasets=[
                        StandaloneDatasetModel(
                            dataset_id="eia_861_annual_energy_use_state_sector"
                        ),
                    ],
                ),
            ),
            result=QueryResultParamsModel(
                aggregations=[
                    AggregationModel(
                        dimensions=DimensionQueryNamesModel(
                            geography=["state"],
                            metric=["end_use"],
                            model_year=["model_year"],
                            scenario=[],
                            sector=["sector"],
                            subsector=[],
                            time=["time_est"],
                            weather_year=["weather_2012"],
                        ),
                        aggregation_function="sum",
                    ),
                ],
                output_format="parquet",
            ),
        )
        return self._model

    def validate(self, expected_values=None):
        df = read_parquet(self.output_dir / self.name / "table.parquet")
        distinct_model_years = df.select(DimensionType.MODEL_YEAR.value).distinct().collect()
        assert len(distinct_model_years) == 1
        assert distinct_model_years[0][DimensionType.MODEL_YEAR.value] == "2020"
        expected_ca_res = calc_expected_eia_861_ca_res_load_value()
        actual_ca_res = (
            df.filter("state == 'CA' and sector == 'res'")
            .agg(F.sum("electricity_unspecified").alias("total_electricity"))
            .collect()[0]
            .total_electricity
        )
        assert math.isclose(actual_ca_res, expected_ca_res)


class QueryTestElectricityValuesCompositeDataset(QueryTestBase):

    NAME = "electricity-values"

    def make_query(self):
        self._model = CreateCompositeDatasetQueryModel(
            name=self.NAME,
            dataset_id="com_res",
            project=ProjectQueryParamsModel(
                project_id=self.get_project_id(),
                include_dsgrid_dataset_components=False,
                dataset=DatasetModel(
                    dataset_id="resstock_conus_2022_projected",
                    source_datasets=[
                        ExponentialGrowthDatasetModel(
                            dataset_id="resstock_conus_2022_projected",
                            initial_value_dataset_id="resstock_conus_2022_reference",
                            growth_rate_dataset_id="aeo2021_reference_residential_energy_use_growth_factors",
                            construction_method="formula123",
                        ),
                    ],
                    params=ProjectQueryDatasetParamsModel(
                        dimension_filters=[
                            SupplementalDimensionFilterColumnOperatorModel(
                                dimension_type=DimensionType.METRIC,
                                dimension_query_name="electricity",
                            ),
                        ],
                    ),
                ),
            ),
        )
        return self._model

    def validate(self, expected_values=None):
        df = read_parquet(
            str(self.output_dir / "composite_datasets" / self._model.dataset_id / "table.parquet")
        )
        assert "natural_gas_heating" not in df.columns
        non_value_columns = self._project.config.get_base_dimension_query_names()
        non_value_columns.update({"id", "timestamp"})
        non_value_columns.update(self._model.result.supplemental_columns)
        value_columns = sorted((x for x in df.columns if x not in non_value_columns))
        expected = ["electricity_cooling", "electricity_heating"]
        # expected = ["electricity_cooling", "electricity_ev_l1l2", "electricity_heating", "fraction"]
        assert value_columns == expected
        assert not set(self._model.result.supplemental_columns).difference(df.columns)

        total_cooling = df.agg(F.sum("electricity_cooling").alias("sum")).collect()[0].sum
        total_heating = df.agg(F.sum("electricity_heating").alias("sum")).collect()[0].sum
        expected = self.get_raw_stats()["overall"]["resstock"]["sum"]
        assert math.isclose(total_cooling, expected["electricity_cooling"])
        assert math.isclose(total_heating, expected["electricity_heating"])


class QueryTestElectricityValuesCompositeDatasetAgg(QueryTestBase):

    NAME = "electricity-values-agg-from-composite-dataset"

    def __init__(self, *args, geography="county", **kwargs):
        super().__init__(*args, **kwargs)
        self._geography = geography

    def make_query(self):
        self._model = CompositeDatasetQueryModel(
            name=self.NAME,
            dataset_id="com_res",
            result=QueryResultParamsModel(
                aggregations=[
                    AggregationModel(
                        dimensions=DimensionQueryNamesModel(
                            geography=[self._geography],
                            metric=["electricity_collapsed"],
                            model_year=[],
                            scenario=[],
                            sector=[],
                            subsector=[],
                            time=[],
                            weather_year=[],
                        ),
                        aggregation_function="sum",
                    ),
                ],
                output_format="parquet",
            ),
        )
        return self._model

    def validate(self, expected_values=None):
        if self._geography == "county":
            validate_electricity_use_by_county(
                "sum",
                self.output_dir / self.name / "table.parquet",
                self.get_raw_stats(),
                4,
            )
        elif self._geography == "state":
            validate_electricity_use_by_state(
                "sum",
                self.output_dir / self.name / "table.parquet",
                self.get_raw_stats(),
            )
        logger.error(
            "Validation is not supported with geography=%s",
            self._geography,
        )
        assert False


class QueryTestUnitMapping(QueryTestBase):

    NAME = "test_efs_comstock_query"

    @staticmethod
    def get_database_name():
        return "cached-test-dsgrid"

    @staticmethod
    def get_project_id():
        return "test_efs"

    def make_query(self):
        self._model = ProjectQueryModel(
            name=self.NAME,
            project=ProjectQueryParamsModel(
                project_id=self.get_project_id(),
                include_dsgrid_dataset_components=False,
                dataset=DatasetModel(
                    dataset_id="efs_comstock",
                    source_datasets=[
                        StandaloneDatasetModel(dataset_id="test_efs_comstock"),
                    ],
                ),
            ),
            result=QueryResultParamsModel(
                output_format="parquet",
            ),
        )
        return self._model

    def validate(self, expected_values=None):
        filename = self.output_dir / self.name / "table.parquet"
        df = read_parquet(filename)
        project = get_project(self.get_database_name(), self.get_project_id())
        project.load_dataset("test_efs_comstock")
        dataset = project.get_dataset("test_efs_comstock")
        ld = dataset._handler._load_data
        lk = dataset._handler._load_data_lookup
        raw_ld = ld.join(lk, on="id").drop("id")
        # This test dataset has some fractional mapping values included.
        # subsector = hospital and model_year = 2020 are 1.0, fans are 1.0
        expected = (
            raw_ld.sort("timestamp").filter("subsector == 'com__Hospital'").limit(1).collect()[0]
        )
        subsector = expected.subsector.replace("com__", "")
        actual = (
            df.filter(
                f"comstock_building_type == '{subsector}' and county == '{expected.geography}' and model_year == '2020'"
            )
            .sort("2012_hourly_est")
            .limit(1)
            .collect()[0]
        )
        assert actual.fans == expected.com_fans * 0.9
        assert actual.cooling == expected.com_cooling * 1000


def perform_op(df, column, operation):
    return df.select(column).agg(operation(column).alias("tmp_col")).collect()[0].tmp_col


def validate_electricity_use_by_county(op, results_path, raw_stats, expected_county_count):
    spark = SparkSession.builder.appName("dgrid").getOrCreate()
    results = spark.read.parquet(str(results_path))
    counties = [str(x.county) for x in results.select("county").distinct().collect()]
    assert len(counties) == expected_county_count, counties
    stats = raw_stats["by_county"]
    for county in counties:
        col = "all_electricity"
        actual = results.filter(f"county == '{county}'").collect()[0][col]
        expected = stats[county]["comstock_resstock"][op]["electricity"]
        assert math.isclose(actual, expected)


def validate_electricity_use_by_state(op, results_path, raw_stats):
    spark = SparkSession.builder.appName("dgrid").getOrCreate()
    results = spark.read.parquet(str(results_path))
    if op == "sum":
        exp_ca = get_expected_ca_sum_electricity(raw_stats)
        exp_ny = get_expected_ny_sum_electricity(raw_stats)
    else:
        assert op == "max", op
        exp_ca = get_expected_ca_max_electricity(raw_stats)
        exp_ny = get_expected_ny_max_electricity(raw_stats)
    col = "all_electricity"
    actual_ca = results.filter("state == 'CA'").collect()[0][col]
    actual_ny = results.filter("state == 'NY'").collect()[0][col]
    assert math.isclose(actual_ca, exp_ca)
    assert math.isclose(actual_ny, exp_ny)


def get_expected_ca_max_electricity(raw_stats):
    by_county = raw_stats["by_county"]
    return max(
        (
            by_county["06037"]["comstock_resstock"]["max"]["electricity"],
            by_county["06073"]["comstock_resstock"]["max"]["electricity"],
        )
    )


def get_expected_ny_max_electricity(raw_stats):
    by_county = raw_stats["by_county"]
    return max(
        (
            by_county["36047"]["comstock_resstock"]["max"]["electricity"],
            by_county["36081"]["comstock_resstock"]["max"]["electricity"],
        )
    )


def get_expected_ca_sum_electricity(raw_stats):
    by_county = raw_stats["by_county"]
    return (
        by_county["06037"]["comstock_resstock"]["sum"]["electricity"]
        + by_county["06073"]["comstock_resstock"]["sum"]["electricity"]
    )


def get_expected_ny_sum_electricity(raw_stats):
    by_county = raw_stats["by_county"]
    return (
        by_county["36047"]["comstock_resstock"]["sum"]["electricity"]
        + by_county["36081"]["comstock_resstock"]["sum"]["electricity"]
    )


BUILDING_COUNTY_MAPPING = {
    "06037": "G0600370",
    "06073": "G0600730",
    "36047": "G3600470",
    "36081": "G3600810",
}


def generate_raw_stats(path):
    datasets = read_datasets(path)
    stats = {"overall": defaultdict(dict), "by_county": {}}
    for project_county in BUILDING_COUNTY_MAPPING:
        stats["by_county"][project_county] = defaultdict(dict)

    operations = (F.sum, F.max, F.mean)
    for name in Datasets._fields:
        for op in operations:
            table = getattr(datasets, name)
            perform_op_by_electricity(stats["overall"], table, name, op)
            for project_county in stats["by_county"]:
                if name == "tempo":
                    dataset_county = project_county
                else:
                    dataset_county = BUILDING_COUNTY_MAPPING[project_county]
                _table = table.filter(f"geography='{dataset_county}'")
                perform_op_by_electricity(stats["by_county"][project_county], _table, name, op)

    accumulate_stats(stats["overall"])
    for county_stats in stats["by_county"].values():
        accumulate_stats(county_stats)
    return stats


def accumulate_stats(stats):
    com = stats["comstock"]
    res = stats["resstock"]
    tem = stats["tempo"]
    com["sum"]["electricity"] = (
        com["sum"]["electricity_cooling"] + com["sum"]["electricity_heating"]
    )
    res["sum"]["electricity"] = (
        res["sum"]["electricity_cooling"] + res["sum"]["electricity_heating"]
    )
    tem["sum"]["electricity"] = tem["sum"]["L1andL2"]
    com["max"]["electricity"] = max(
        (com["max"]["electricity_cooling"], com["max"]["electricity_heating"])
    )
    res["max"]["electricity"] = max(
        (res["max"]["electricity_cooling"], res["max"]["electricity_heating"])
    )
    tem["max"]["electricity"] = tem["max"]["L1andL2"]
    stats["comstock_resstock"] = {
        "sum": {
            "electricity_cooling": com["sum"]["electricity_cooling"]
            + res["sum"]["electricity_cooling"],
            "electricity_heating": com["sum"]["electricity_heating"]
            + res["sum"]["electricity_heating"],
            "electricity": com["sum"]["electricity"] + res["sum"]["electricity"],
        },
        "max": {
            "electricity_cooling": max(
                (com["max"]["electricity_cooling"], res["max"]["electricity_cooling"])
            ),
            "electricity_heating": max(
                (com["max"]["electricity_heating"], res["max"]["electricity_heating"])
            ),
            "electricity": max((com["max"]["electricity"], res["max"]["electricity"])),
        },
    }
    stats["total"] = {
        "sum": {
            "electricity": com["sum"]["electricity"]
            + res["sum"]["electricity"]
            + tem["sum"]["electricity"],
        },
        "max": {
            "electricity": max(
                (com["max"]["electricity"], res["max"]["electricity"], tem["max"]["electricity"])
            ),
        },
    }


def read_datasets(path):
    aeo_com = map_aeo_com_subsectors(
        map_aeo_com_county_to_comstock_county(
            duplicate_aeo_com_census_division_to_county(
                apply_load_mapping_aeo_com(
                    read_csv_single_table(
                        path
                        / "data"
                        / "aeo2021_reference_commercial_energy_use_growth_factors"
                        / "1.0.0"
                        / "load_data.csv"
                    )
                )
            )
        )
    )
    aeo_res = apply_load_mapping_aeo_res(
        read_csv_single_table(
            path
            / "data"
            / "aeo2021_reference_residential_energy_use_growth_factors"
            / "1.0.0"
            / "load_data.csv"
        ).drop("sector")
    )
    comstock = make_projection_df(
        aeo_com,
        read_table(path / "data" / "comstock_conus_2022_reference" / "1.0.0"),
        ["geography", "subsector", "model_year"],
    )
    resstock = make_projection_df(
        aeo_res,
        read_table(path / "data" / "resstock_conus_2022_reference" / "1.0.0"),
        ["model_year"],
    )
    datasets = Datasets(
        comstock=comstock,
        resstock=resstock,
        tempo=read_dataset_tempo(),
    )
    return datasets


def apply_load_mapping_aeo_com(aeo_com):
    return (
        aeo_com.withColumn("electricity_cooling", F.col("elec_cooling") * 1.0)
        .withColumn("electricity_heating", F.col("elec_heating") * 1.0)
        .drop("elec_cooling", "elec_heating")
    )


def duplicate_aeo_com_census_division_to_county(aeo_com):
    records = get_dim_mapping_records_from_db("US Census Divisions", "US Counties 2020 L48")
    assert records.select("from_fraction").distinct().collect()[0].from_fraction == 1.0
    records = records.drop("from_fraction")
    mapped = aeo_com.join(records, on=aeo_com.geography == records.from_id)
    # Make sure no census division got dropped in the join.
    orig_count = aeo_com.select("geography").distinct().count()
    new_count = mapped.select("geography").distinct().count()
    assert orig_count == new_count, f"{orig_count} {new_count}"
    return mapped.drop("from_id", "geography").withColumnRenamed("to_id", "geography")


def map_aeo_com_county_to_comstock_county(aeo_com):
    records = get_dim_mapping_records_from_db(
        "conus_2022-comstock_US_county_FIP", "US Counties 2020 L48"
    )
    assert records.select("from_fraction").distinct().collect()[0].from_fraction == 1.0
    records = records.drop("from_fraction")
    mapped = aeo_com.join(records, on=aeo_com.geography == records.to_id)
    # Make sure no entries were dropped.
    orig_count = aeo_com.count()
    new_count = mapped.count()
    assert orig_count == new_count, f"{orig_count} {new_count}"
    return mapped.drop("to_id", "geography").withColumnRenamed("from_id", "geography")


def map_aeo_com_subsectors(aeo_com):
    records = get_dim_mapping_records_from_db(
        "AEO2021-commercial-building-types", "CONUS-2022-Detailed-Subsectors"
    )
    mapped = aeo_com.join(records, on=aeo_com.subsector == records.from_id)
    # Make sure no subsector got dropped in the join.
    orig_count = aeo_com.select("subsector").distinct().count()
    new_count = mapped.select("subsector").distinct().count()
    assert orig_count == new_count, f"{orig_count} {new_count}"
    mapped = mapped.drop("from_id", "subsector").withColumnRenamed("to_id", "subsector")
    for col in ("electricity_cooling", "electricity_heating"):
        mapped = mapped.withColumn(col, mapped[col] * mapped["from_fraction"])
    return (
        mapped.drop("from_fraction")
        .groupBy("subsector", "geography")
        .agg(
            F.sum("electricity_cooling").alias("electricity_cooling"),
            F.sum("electricity_heating").alias("electricity_heating"),
            F.sum("ng_heating").alias("ng_heating"),
        )
    )


def get_dim_mapping_records_from_db(from_dim_name, to_dim_name):
    conn = DatabaseConnection(database="simple-standard-scenarios")
    client = RegistryDatabase.connect(conn)
    records = None
    for doc in client.collection("dimension_mappings"):
        from_id = doc["from_dimension"]["dimension_id"]
        to_id = doc["to_dimension"]["dimension_id"]
        from_dim = client.collection("dimensions").find({"dimension_id": from_id}).next()
        to_dim = client.collection("dimensions").find({"dimension_id": to_id}).next()
        if from_dim["name"] == from_dim_name and to_dim["name"] == to_dim_name:
            models = convert_record_dicts_to_classes(doc["records"], MappingTableRecordModel)
            records = models_to_dataframe(models)
    assert records is not None, f"{from_dim_name=} {to_dim_name=}"
    return records


def apply_load_mapping_aeo_res(aeo_res):
    return (
        aeo_res.withColumn("electricity_cooling", F.col("elec_heat_cool") * 1.0)
        .withColumn("electricity_heating", F.col("elec_heat_cool") * 1.0)
        .drop("elec_heat_cool")
    )


def make_projection_df(aeo, ld_df, join_columns):
    # comstock and resstock have a single year of data for model_year 2018
    # Apply the growth rate for 2020 and 2040, the years in the filtered registry.
    spark = SparkSession.builder.appName("dgrid").getOrCreate()
    years_df = spark.createDataFrame([{"model_year": "2020"}, {"model_year": "2040"}])
    aeo = aeo.crossJoin(years_df)
    ld_df = ld_df.crossJoin(years_df)
    base_year = 2018
    gr_df = aeo
    pivoted_columns = ("electricity_cooling", "electricity_heating")
    for column in pivoted_columns:
        gr_col = column + "__gr"
        gr_df = gr_df.withColumn(
            gr_col,
            F.pow((1 + F.col(column)), F.col("model_year").cast(IntegerType()) - base_year),
        ).drop(column)

    df = ld_df.join(gr_df, on=join_columns)
    for column in pivoted_columns:
        gr_col = column + "__gr"
        df = df.withColumn(column, df[column] * df[gr_col]).drop(gr_col)

    return df.cache()


def calc_expected_eia_861_ca_res_load_value():
    project = get_project("simple-standard-scenarios", "dsgrid_conus_2022")
    dataset_id = dataset_id = "eia_861_annual_energy_use_state_sector"
    project.load_dataset(dataset_id)
    mapping_id = None
    for dataset in project.config.model.datasets:
        if dataset.dataset_id == "eia_861_annual_energy_use_state_sector":
            for ref in dataset.mapping_references:
                if ref.from_dimension_type == DimensionType.GEOGRAPHY:
                    mapping_id = ref.mapping_id
                    break
        if mapping_id is not None:
            break
    assert mapping_id is not None
    records = project.dimension_mapping_manager.get_by_id(mapping_id).get_records_dataframe()

    fraction_06037 = records.filter("to_id == '06037'").collect()[0].from_fraction
    fraction_06073 = records.filter("to_id == '06073'").collect()[0].from_fraction
    dataset = project.get_dataset(dataset_id)
    raw = dataset._handler._load_data.filter("geography == 'CA' and sector == 'res'").collect()
    assert len(raw) == 1
    num_scenarios = 2
    elec_kwh_state = raw[0].electricity_sales * 1000 * num_scenarios
    elec_kwh_selected_counties = elec_kwh_state * fraction_06037 + elec_kwh_state * fraction_06073
    return elec_kwh_selected_counties


def read_dataset_tempo():
    project = get_project("simple-standard-scenarios", "dsgrid_conus_2022")
    dataset_id = dataset_id = "tempo_conus_2022"
    project.load_dataset(dataset_id)
    tempo = project.get_dataset(dataset_id)
    lookup = tempo._handler._load_data_lookup
    load_data = tempo._handler._load_data
    tempo_data_mapped_time = tempo._handler._convert_time_dimension(
        load_data.join(lookup, on="id").drop("id"), project.config, [], ["L1andL2"]
    )
    return tempo_data_mapped_time.cache()


def read_csv_single_table(path):
    spark = SparkSession.builder.appName("dgrid").getOrCreate()
    return spark.read.csv(str(path), header=True, inferSchema=True)


def read_table(path):
    spark = SparkSession.builder.appName("dgrid").getOrCreate()
    load_data = spark.read.parquet(str(path / "load_data.parquet")).cache()
    lookup = spark.read.parquet(str(path / "load_data_lookup.parquet")).cache()
    table = load_data.join(lookup, on="id").drop("id").cache()
    return table


def perform_op_by_electricity(stats, table, name, operation):
    if name in ("comstock", "resstock"):
        columns = ["electricity_cooling", "electricity_heating"]
    elif name == "tempo":
        columns = ["L1andL2"]
    else:
        assert False, name
    for col in columns:
        op = operation.__name__
        col_name = f"{op}_{col}"
        if op not in stats[name]:
            stats[name][op] = {}
        val = getattr(
            table.agg(operation(col).alias(col_name)).collect()[0],
            col_name,
        )
        if op == "sum":
            # 2 scenarios
            val *= 2
        stats[name][op][col] = val
    stats[name]["count"] = table.count()


def read_parquet(filename):
    """Read a Parquet file and load it into cache. This helps debugging with pytest --pdb.
    If you don't use this, the parquet file will get deleted on a failure and you won't be able
    to inspect the dataframe.
    """
    spark = SparkSession.builder.appName("dgrid").getOrCreate()
    df = spark.read.parquet(str(filename)).cache()
    df.count()
    return df


# The next two functions are for ad hoc testing.


def run_query(
    dimension_query_name,
    registry_path=REGISTRY_PATH,
    operation="sum",
    output_dir=Path("queries"),
    persist_intermediate_table=True,
    load_cached_table=True,
):
    setup_logging(
        "dsgrid", "query.log", console_level=logging.INFO, file_level=logging.INFO, mode="w"
    )
    project = Project.load(
        "dsgrid_conus_2022",
        offline_mode=True,
        registry_path=registry_path,
    )
    if dimension_query_name == QueryTestElectricityValues.NAME:
        query = QueryTestElectricityValues(True, registry_path, project, output_dir=output_dir)
    else:
        raise Exception(f"no query for {dimension_query_name}")

    ProjectQuerySubmitter(project, output_dir).submit(
        query.make_query(),
        persist_intermediate_table=persist_intermediate_table,
        load_cached_table=load_cached_table,
        force=True,
    )
    result = query.validate()
    print(f"Result of query {query.name} = {result}")


def run_composite_dataset(
    registry_path=REGISTRY_PATH,
    output_dir=Path("queries"),
    persist_intermediate_table=False,
    load_cached_table=True,
):
    setup_logging(
        "dsgrid", "query.log", console_level=logging.INFO, file_level=logging.INFO, mode="w"
    )
    conn = DatabaseConnection(database="simple_standard_scenarios")
    mgr = RegistryManager.load(
        conn,
        offline_mode=True,
    )
    project = mgr.project_manager.load_project("dsgrid_conus_2022")
    query = QueryTestElectricityValuesCompositeDataset(
        registry_path, project, output_dir=output_dir
    )
    CompositeDatasetQuerySubmitter(project, output_dir).create_dataset(
        query.make_query(),
        persist_intermediate_table=persist_intermediate_table,
        load_cached_table=load_cached_table,
    )
    result = query.validate()
    print(f"Result of query {query.name} = {result}")

    query2 = QueryTestElectricityValuesCompositeDatasetAgg(
        registry_path, project, output_dir=output_dir, geography="county"
    )
    CompositeDatasetQuerySubmitter(project, output_dir).submit(query2.make_query())
    result = query2.validate()
    print(f"Result of query {query2.name} = {result}")

    query3 = QueryTestElectricityValuesCompositeDatasetAgg(
        registry_path, project, output_dir=output_dir, geography="state"
    )
    CompositeDatasetQuerySubmitter(project, output_dir).submit(query3.make_query())
    result = query3.validate()
    print(f"Result of query {query3.name} = {result}")
