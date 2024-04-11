from enum import Enum

from sentry import options
from sentry.llm.providers.base import LlmModelBase
from sentry.llm.types import ModelLiterals, ProviderOptions, UseCaseOptions
from sentry.utils.services import LazyServiceWrapper

SENTRY_LLM_SERVICE_ALIASES: dict[ModelLiterals, str] = {
    "vertex": "sentry.llm.providers.vertex.VertexProvider",
    "openai": "sentry.llm.providers.openai.OpenAIProvider",
    "preview": "sentry.llm.providers.preview.PreviewLLM",
}


class LlmUseCase(Enum):
    EXAMPLE = "example"  # used in tests / examples
    SUGGESTED_FIX = "suggestedfix"  # OG version of suggested fix


llm_provider_backends = {}


def get_llm_provider_backend(usecase: LlmUseCase) -> LlmModelBase:
    usecase_config: UseCaseOptions = get_usecase_options(usecase.value)
    provider_config: ProviderOptions = get_provider_options()
    global llm_provider_backends

    if usecase_config["provider"] in llm_provider_backends:
        return llm_provider_backends[usecase_config["provider"]]

    llm_provider_backends[usecase_config["provider"]] = LazyServiceWrapper(
        LlmModelBase,
        SENTRY_LLM_SERVICE_ALIASES[usecase_config["provider"]],
        provider_config,
    )
    return llm_provider_backends[usecase_config["provider"]]


def complete_prompt(
    usecase: LlmUseCase, prompt: str, message: str, temperature: float, max_output_tokens: int
) -> str | None:
    usecase_config: UseCaseOptions = get_usecase_options(usecase.value)
    provider_config: ProviderOptions = get_provider_options()

    backend = LazyServiceWrapper(
        LlmModelBase,
        SENTRY_LLM_SERVICE_ALIASES[usecase_config["provider"]],
        provider_config,
    )
    return backend.complete_prompt(usecase_config, prompt, message, temperature, max_output_tokens)


def get_usecase_options(usecase: str) -> UseCaseOptions:
    usecase_options_all: UseCaseOptions = options.get("llm.usecases.options")
    if not usecase_options_all:
        raise ValueError("LLM usecase options not found. please check llm.usecases.options")

    usecase_options = usecase_options_all.get(usecase)
    if not usecase_options:
        raise ValueError(
            f"LLM usecase options not found for {usecase}. please check llm.usecases.options"
        )

    return usecase_options


def get_provider_options() -> ProviderOptions:
    llm_provider_option: ProviderOptions = options.get("llm.provider.options")
    if not llm_provider_option:
        raise ValueError("LLM provider option not found")
    return llm_provider_option
