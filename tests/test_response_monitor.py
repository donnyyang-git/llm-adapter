import pytest

from llm_adapter.adapters.gemini import GeminiResponse
from llm_adapter.response_monitor import ResponseMonitor, ResponseTimeoutError


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.value += seconds


def response(
    text: str,
    *,
    generating: bool,
    input_editable: bool,
) -> GeminiResponse:
    return GeminiResponse(
        text=text,
        markdown=text,
        generating=generating,
        input_editable=input_editable,
    )


@pytest.mark.asyncio
async def test_completes_only_after_content_is_stable_and_ui_is_ready() -> None:
    clock = FakeClock()
    samples = [
        response("Answer", generating=True, input_editable=False),
        response("Answer", generating=False, input_editable=True),
        response("Answer", generating=False, input_editable=True),
    ]
    updates: list[str] = []

    async def sample() -> GeminiResponse:
        return samples.pop(0) if len(samples) > 1 else samples[0]

    async def on_update(current: GeminiResponse) -> None:
        updates.append(current.text)

    monitor = ResponseMonitor(3, 10, 2, 1, monotonic=clock.monotonic, sleep=clock.sleep)
    result = await monitor.wait(sample, on_update)

    assert result.text == "Answer"
    assert clock.value == 2
    assert updates == ["Answer"]


@pytest.mark.asyncio
async def test_first_response_timeout_has_no_partial() -> None:
    clock = FakeClock()

    async def sample() -> None:
        return None

    monitor = ResponseMonitor(2, 10, 1, 1, monotonic=clock.monotonic, sleep=clock.sleep)

    with pytest.raises(ResponseTimeoutError) as captured:
        await monitor.wait(sample)

    assert captured.value.phase == "first_response"
    assert captured.value.partial is None


@pytest.mark.asyncio
async def test_total_timeout_preserves_latest_partial_response() -> None:
    clock = FakeClock()
    counter = 0

    async def sample() -> GeminiResponse:
        nonlocal counter
        counter += 1
        return response(f"Part {counter}", generating=True, input_editable=False)

    monitor = ResponseMonitor(2, 3, 1, 1, monotonic=clock.monotonic, sleep=clock.sleep)

    with pytest.raises(ResponseTimeoutError) as captured:
        await monitor.wait(sample)

    assert captured.value.phase == "total"
    assert captured.value.partial is not None
    assert captured.value.partial.text == "Part 3"


@pytest.mark.asyncio
async def test_transient_missing_dom_does_not_discard_partial_response() -> None:
    clock = FakeClock()
    samples = [response("Partial", generating=True, input_editable=False), None]

    async def sample() -> GeminiResponse | None:
        return samples.pop(0) if len(samples) > 1 else samples[0]

    monitor = ResponseMonitor(2, 3, 1, 1, monotonic=clock.monotonic, sleep=clock.sleep)

    with pytest.raises(ResponseTimeoutError) as captured:
        await monitor.wait(sample)

    assert captured.value.phase == "total"
    assert captured.value.partial is not None
    assert captured.value.partial.text == "Partial"