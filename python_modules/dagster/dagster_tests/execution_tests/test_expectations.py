from typing import Sequence

import pytest

from dagster import DagsterEventType, DagsterInvariantViolationError, ExpectationResult
from dagster._core.events import DagsterEvent
from dagster._core.execution.results import OpExecutionResult, PipelineExecutionResult
from dagster._legacy import PipelineDefinition, execute_pipeline, solid


def expt_results_for_compute_step(
    result: PipelineExecutionResult, solid_name: str
) -> Sequence[DagsterEvent]:
    solid_result = result.result_for_node(solid_name)
    assert isinstance(solid_result, OpExecutionResult)
    return [
        compute_step_event
        for compute_step_event in solid_result.compute_step_events
        if compute_step_event.event_type == DagsterEventType.STEP_EXPECTATION_RESULT
    ]


def test_successful_expectation_in_compute_step():
    @solid(output_defs=[])
    def success_expectation_solid(_context):
        yield ExpectationResult(success=True, description="This is always true.")

    pipeline_def = PipelineDefinition(
        name="success_expectation_in_compute_pipeline",
        solid_defs=[success_expectation_solid],
    )

    result = execute_pipeline(pipeline_def)

    assert result
    assert result.success

    expt_results = expt_results_for_compute_step(result, "success_expectation_solid")

    assert len(expt_results) == 1
    expt_result = expt_results[0]
    assert expt_result.event_specific_data.expectation_result.success
    assert expt_result.event_specific_data.expectation_result.description == "This is always true."


def test_failed_expectation_in_compute_step():
    @solid(output_defs=[])
    def failure_expectation_solid(_context):
        yield ExpectationResult(success=False, description="This is always false.")

    pipeline_def = PipelineDefinition(
        name="failure_expectation_in_compute_pipeline",
        solid_defs=[failure_expectation_solid],
    )

    result = execute_pipeline(pipeline_def)

    assert result
    assert result.success
    expt_results = expt_results_for_compute_step(result, "failure_expectation_solid")

    assert len(expt_results) == 1
    expt_result = expt_results[0]
    assert not expt_result.event_specific_data.expectation_result.success
    assert expt_result.event_specific_data.expectation_result.description == "This is always false."


def test_return_expectation_failure():
    @solid(output_defs=[])
    def return_expectation_failure(_context):
        return ExpectationResult(success=True, description="This is always true.")

    pipeline_def = PipelineDefinition(
        name="success_expectation_in_compute_pipeline",
        solid_defs=[return_expectation_failure],
    )

    with pytest.raises(
        DagsterInvariantViolationError,
        match="If you are returning an AssetMaterialization or an ExpectationResult from op you must yield them directly",
    ):
        execute_pipeline(pipeline_def)
