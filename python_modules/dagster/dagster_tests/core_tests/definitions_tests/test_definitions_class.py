import pytest

from dagster import (
    AssetKey,
    AssetsDefinition,
    Definitions,
    ResourceDefinition,
    ScheduleDefinition,
    SourceAsset,
    asset,
    define_asset_job,
    materialize,
    op,
    repository,
    sensor,
)
from dagster._check import CheckError
from dagster._core.definitions.cacheable_assets import (
    AssetsDefinitionCacheableData,
    CacheableAssetsDefinition,
)
from dagster._core.definitions.decorators.job_decorator import job
from dagster._core.definitions.executor_definition import executor
from dagster._core.definitions.job_definition import JobDefinition
from dagster._core.definitions.logger_definition import logger
from dagster._core.definitions.repository_definition import (
    PendingRepositoryDefinition,
    RepositoryDefinition,
)
from dagster._core.storage.io_manager import IOManagerDefinition
from dagster._core.storage.mem_io_manager import InMemoryIOManager
from dagster._core.test_utils import instance_for_test


def get_all_assets_from_defs(defs: Definitions):
    # could not find public method on repository to do this
    repo = resolve_pending_repo_if_required(defs)
    return list(repo._assets_defs_by_key.values())  # pylint: disable=protected-access


def resolve_pending_repo_if_required(definitions: Definitions) -> RepositoryDefinition:
    repo_or_caching_repo = definitions.get_inner_repository_for_loading_process()
    return (
        repo_or_caching_repo.compute_repository_definition()
        if isinstance(repo_or_caching_repo, PendingRepositoryDefinition)
        else repo_or_caching_repo
    )


def test_basic_asset():
    assert Definitions  # type: ignore

    @asset
    def an_asset():
        pass

    defs = Definitions(assets=[an_asset])

    all_assets = get_all_assets_from_defs(defs)
    assert len(all_assets) == 1
    assert all_assets[0].key.to_user_string() == "an_asset"


def test_basic_asset_job_definition():
    @asset
    def an_asset():
        pass

    defs = Definitions(assets=[an_asset], jobs=[define_asset_job(name="an_asset_job")])

    assert isinstance(defs.get_job_def("an_asset_job"), JobDefinition)


def test_vanilla_job_definition():
    @op
    def an_op():
        pass

    @job
    def a_job():
        pass

    defs = Definitions(jobs=[a_job])
    assert isinstance(defs.get_job_def("a_job"), JobDefinition)


def test_basic_schedule_definition():
    @asset
    def an_asset():
        pass

    defs = Definitions(
        assets=[an_asset],
        schedules=[
            ScheduleDefinition(
                name="daily_an_asset_schedule",
                job=define_asset_job(name="an_asset_job"),
                cron_schedule="@daily",
            )
        ],
    )

    assert defs.get_schedule_def("daily_an_asset_schedule")


def test_basic_sensor_definition():
    @asset
    def an_asset():
        pass

    an_asset_job = define_asset_job(name="an_asset_job")

    @sensor(name="an_asset_sensor", job=an_asset_job)
    def a_sensor():
        raise NotImplementedError()

    defs = Definitions(
        assets=[an_asset],
        sensors=[a_sensor],
    )

    assert defs.get_sensor_def("an_asset_sensor")


def test_with_resource_binding():
    executed = {}

    @asset(required_resource_keys={"foo"})
    def requires_foo(context):
        assert context.resources.foo == "wrapped"
        executed["yes"] = True

    defs = Definitions(
        assets=[requires_foo],
        resources={"foo": ResourceDefinition.hardcoded_resource("wrapped")},
    )
    repo = resolve_pending_repo_if_required(defs)
    asset_job = repo.get_all_jobs()[0]
    asset_job.execute_in_process()
    assert executed["yes"]


def test_resource_coercion():
    executed = {}

    @asset(required_resource_keys={"foo"})
    def requires_foo(context):
        assert context.resources.foo == "object-to-coerce"
        executed["yes"] = True

    defs = Definitions(
        assets=[requires_foo],
        resources={"foo": "object-to-coerce"},
    )
    repo = resolve_pending_repo_if_required(defs)
    asset_job = repo.get_all_jobs()[0]
    asset_job.execute_in_process()
    assert executed["yes"]


def test_source_asset():
    defs = Definitions(assets=[SourceAsset("a-source-asset")])
    repo = resolve_pending_repo_if_required(defs)
    all_assets = list(repo.source_assets_by_key.values())
    assert len(all_assets) == 1
    assert all_assets[0].key.to_user_string() == "a-source-asset"


def test_pending_repo():
    class MyCacheableAssetsDefinition(CacheableAssetsDefinition):
        def compute_cacheable_data(self):
            return [
                AssetsDefinitionCacheableData(
                    keys_by_input_name={}, keys_by_output_name={"result": AssetKey(self.unique_id)}
                )
            ]

        def build_definitions(self, data):
            @op
            def my_op():
                return 1

            return [
                AssetsDefinition.from_op(
                    my_op,
                    keys_by_input_name=cd.keys_by_input_name,
                    keys_by_output_name=cd.keys_by_output_name,
                )
                for cd in data
            ]

    # This section of the test was just to test my understanding of what is happening
    # here and then it also documents why resolve_pending_repo_if_required is necessary
    @repository
    def a_pending_repo():
        return [MyCacheableAssetsDefinition("foobar")]

    assert isinstance(a_pending_repo, PendingRepositoryDefinition)
    assert isinstance(a_pending_repo.compute_repository_definition(), RepositoryDefinition)

    # now actually test definitions

    defs = Definitions(assets=[MyCacheableAssetsDefinition("foobar")])
    all_assets = get_all_assets_from_defs(defs)
    assert len(all_assets) == 1
    assert all_assets[0].key.to_user_string() == "foobar"

    assert isinstance(defs.get_job_def("__ASSET_JOB"), JobDefinition)


def test_asset_loading():
    @asset
    def one():
        return 1

    @asset
    def two():
        return 2

    with instance_for_test() as instance:
        defs = Definitions(assets=[one, two])
        materialize(assets=[one, two], instance=instance)
        assert defs.load_asset_value("one", instance=instance) == 1
        assert defs.load_asset_value("two", instance=instance) == 2

        value_loader = defs.get_asset_value_loader(instance)
        assert value_loader.load_asset_value("one") == 1
        assert value_loader.load_asset_value("two") == 2


def test_io_manager_coercion():
    @asset(io_manager_key="mem_io_manager")
    def one():
        return 1

    defs = Definitions(assets=[one], resources={"mem_io_manager": InMemoryIOManager()})

    asset_job = defs.get_job_def("__ASSET_JOB")
    assert isinstance(asset_job.resource_defs["mem_io_manager"], IOManagerDefinition)
    result = asset_job.execute_in_process()
    assert result.output_for_node("one") == 1


def test_bad_executor():
    with pytest.raises(CheckError):
        # ignoring type to catch runtime error
        Definitions(executor="not an executor")  # type: ignore


def test_custom_executor_in_definitions():
    @executor
    def an_executor(_):
        raise Exception("not executed")

    @asset
    def one():
        return 1

    defs = Definitions(assets=[one], executor=an_executor)
    asset_job = defs.get_job_def("__ASSET_JOB")
    assert asset_job.executor_def is an_executor


def test_custom_loggers_in_definitions():
    @logger
    def a_logger(_):
        raise Exception("not executed")

    @asset
    def one():
        return 1

    defs = Definitions(assets=[one], loggers={"custom_logger": a_logger})

    asset_job = defs.get_job_def("__ASSET_JOB")
    loggers = asset_job.loggers
    assert len(loggers) == 1
    assert "custom_logger" in loggers
    assert loggers["custom_logger"] is a_logger


def test_bad_logger_key():
    @logger
    def a_logger(_):
        raise Exception("not executed")

    with pytest.raises(CheckError):
        # ignore type to catch runtime error
        Definitions(loggers={1: a_logger})  # type: ignore


def test_bad_logger_value():
    with pytest.raises(CheckError):
        # ignore type to catch runtime error
        Definitions(loggers={"not_a_logger": "not_a_logger"})  # type: ignore
