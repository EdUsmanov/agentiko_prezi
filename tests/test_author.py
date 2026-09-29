import asyncio
from types import SimpleNamespace
import httpx
from studio.contents.author import expand_brief


class FakeGateway:
    settings = SimpleNamespace(mode="api")

    def __init__(self, proposals):
        self.proposals = proposals

    async def json_request(self, *args, **kwargs):
        return {"proposals": self.proposals}


class SequenceGateway(FakeGateway):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.payloads = []

    async def json_request(self, stage, payload, **kwargs):
        self.payloads.append(payload)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def test_brief_drafts_are_labelled_and_do_not_mutate_prepared(prepared):
    _, _, package = prepared
    package.content.facts = package.content.facts[:1]
    package.constraints.slides = 2
    updated, warning = asyncio.run(
        expand_brief(
            package,
            FakeGateway(
                ["Предлагается начать с пилотного процесса и согласовать критерии оценки."]
            ),
            10,
        )
    )
    assert warning is None
    assert len(package.content.facts) == 1
    assert len(updated.content.facts) == 2
    assert updated.content.facts[-1].source == "model_proposal"
    assert updated.content.facts[-1].text.startswith("Предлагается")


def test_reject_invented_numbers_in_drafts(prepared):
    _, _, package = prepared
    package.content.facts = package.content.facts[:1]
    package.constraints.slides = 2
    updated, warning = asyncio.run(
        expand_brief(
            package,
            FakeGateway(["Ожидаемый эффект составит 99 процентов за месяц после внедрения."]),
            10,
        )
    )
    assert warning
    assert len(updated.content.facts) == 1


def test_invalid_model_proposal_retried_with_specific_feedback(prepared):
    _, _, package = prepared
    package.content.facts = package.content.facts[:1]
    package.constraints.slides = 2
    gateway = SequenceGateway(
        [
            {
                "proposals": [
                    "Предлагается оценить результат за 12 недель и обсудить продолжение пилота."
                ]
            },
            {
                "proposals": [
                    "Предлагается обсудить критерии оценки пилота и условия его продолжения."
                ]
            },
        ]
    )
    updated, warning = asyncio.run(expand_brief(package, gateway, 10))
    assert warning is None
    assert len(gateway.payloads) == 2
    assert "содержит число" in gateway.payloads[1]["validation_feedback"]
    assert len(package.content.facts) == 1
    assert updated.content.facts[-1].text == (
        "Предлагается обсудить критерии оценки пилота и условия его продолжения."
    )


def test_author_fatal_provider_error_is_not_retried(prepared):
    _, _, package = prepared
    package.content.facts = package.content.facts[:1]
    package.constraints.slides = 2
    request = httpx.Request("POST", "https://example.invalid/model")
    error = httpx.HTTPStatusError(
        "Invalid credentials", request=request, response=httpx.Response(401, request=request)
    )
    gateway = SequenceGateway([error])
    updated, warning = asyncio.run(expand_brief(package, gateway, 10))
    assert warning and "InductionFailure" in warning
    assert len(gateway.payloads) == 1
    assert updated is package
