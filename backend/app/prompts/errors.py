class PromptRegistryError(RuntimeError):
    code = "PROMPT_REGISTRY_ERROR"


class PromptConflictError(PromptRegistryError):
    code = "PROMPT_VERSION_CONFLICT"


class PromptNotFrozenError(PromptRegistryError):
    code = "PROMPT_NOT_FROZEN"
