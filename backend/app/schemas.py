from __future__ import annotations
from pydantic import field_validator

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


LAN_SYNC_MAX_CHANGE_ROWS = 10_000
LAN_SYNC_MAX_REQUEST_BYTES = 16 * 1024 * 1024


class PracticeCreate(BaseModel):
    mode: Literal["paper", "unit", "random", "wrong", "wrong_history"]
    paper_id: int | None = None
    unit_ids: list[int] = Field(default_factory=list)
    question_ids: list[int] = Field(default_factory=list)
    history_round_id: int | None = None
    unit_type: str | None = None
    selection_scope: Literal["unit", "paper_unit_type"] = "unit"
    count: int = 1
    shuffle_options: bool = True
    force_new: bool = False


class UpdateOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class QuestionBankProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class QuestionBankProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)


class BatchPaperMoveRequest(BaseModel):
    paper_ids: list[int] = Field(min_length=1, max_length=200)
    target_profile_id: int


class TrashRestoreRequest(BaseModel):
    target_profile_id: int | None = None


class AnswerUpdate(BaseModel):
    answer: str
    option_order: list[str] = Field(default_factory=list)


class AiSettingsUpdate(BaseModel):
    name: str = "本地模型"
    adapter: str = "openai-chat"
    base_url: str
    api_key: str | None = None
    model: str
    temperature: float = 0.2
    max_tokens: int = Field(default=0, ge=0)
    reasoning_effort: str = ""
    system_prompt: str = ""


class AiModelListRequest(BaseModel):
    base_url: str
    adapter: str = "openai-chat"
    api_key: str | None = None
    use_saved_api_key: bool = False
    profile_id: int | None = None


class AiProfileWrite(BaseModel):
    name: str = "本地模型"
    adapter: str = "openai-chat"
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key: str | None = None
    clear_api_key: bool = False
    enabled: bool = True
    is_default: bool = False
    default_model: str = ""
    temperature: float = 0.2
    max_tokens: int = Field(default=0, ge=0)
    reasoning_effort: str = ""
    system_prompt: str = ""


class AiModelVisibilityUpdate(BaseModel):
    model_id: str
    is_visible: bool


class AiModelsVisibilityUpdate(BaseModel):
    is_visible: bool


class AiProfileTestRequest(BaseModel):
    model: str | None = None


class QuestionBankCatalogSettingsWrite(BaseModel):
    question_bank_catalog_url: str = Field(default="", max_length=2048)


class QuestionBankDownloadRequest(BaseModel):
    package_id: str = Field(min_length=1, max_length=80)
    content_version: str = Field(min_length=1, max_length=80)


class AiChatAttachment(BaseModel):
    name: str = Field(default="图片", max_length=80)
    data_url: str


class AiChatRequest(BaseModel):
    conversation_id: int | None = None
    profile_id: int
    model: str
    message: str = Field(default="", max_length=20000)
    attachments: list[AiChatAttachment] = Field(default_factory=list)
    reasoning_effort: str | None = None


class AiAnalyzeRequest(BaseModel):
    question_ids: list[int] = Field(default_factory=list)
    focus: str = ""
    scope_title: str = ""


class AiLabelBatchRequest(BaseModel):
    year: int | None = None
    paper_ids: list[int] = Field(default_factory=list, max_length=100)
    overwrite_unlocked: bool = False
    run_id: str = Field(default="", max_length=80)
    question_bank_profile_id: int | None = Field(default=None, gt=0)
    profile_id: int | None = None
    model: str = ""
    max_tokens: int | None = Field(default=None, ge=0)


class AiQuestionLabelUpdate(BaseModel):
    primary_skill: str
    secondary_skills: list[str] = Field(default_factory=list)
    trap_types: list[str] = Field(default_factory=list)
    attention_points: list[str] = Field(default_factory=list)
    vocabulary_demand: Literal["low", "medium", "high"] = "medium"
    context_dependency: Literal["low", "medium", "high"] = "medium"
    grammar_dependency: Literal["low", "medium", "high"] = "medium"
    confidence: float = Field(default=1, ge=0, le=1)
    locked: bool = True


class DraftUpdate(BaseModel):
    draft_data: dict[str, Any]
    reason: str = "用户编辑"


class ImportAnswersUpdate(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    reason: str = "人工录入标准答案"


class QuestionBankConflictResolution(BaseModel):
    paper_key: str = Field(min_length=3, max_length=200)
    action: Literal["keep_existing", "replace_with_imported"]


class QuestionBankPublishRequest(BaseModel):
    resolutions: list[QuestionBankConflictResolution] = Field(default_factory=list)
    import_ai_labels: bool = True


class AiCorrectionRequest(BaseModel):
    scope: Literal["all", "passage", "questions", "answers"] = "all"
    instructions: str = ""


class ModelAssistRequest(BaseModel):
    profile_id: int | None = None
    model: str = ""
    correct_structure: bool = False
    # Kept for backward compatibility. The application does not send an
    # output-token cap; the selected provider/model decides its own limit.
    max_tokens: int | None = Field(default=None, ge=0)


class VocabularyCreate(BaseModel):
    term: str
    source_kind: Literal["unknown", "passage", "option", "question", "fragment"] = "unknown"
    selection_start: int = Field(default=-1, ge=-1)
    context_sentence: str = ""
    context_before: str = ""
    context_after: str = ""
    unit_id: int | None = None
    question_id: int | None = None
    year: int | None = None
    unit_title: str = ""
    unit_type: str = ""


class VocabularyTranslationRunRequest(BaseModel):
    entry_ids: list[int] = Field(default_factory=list, max_length=100)
    trigger: Literal[
        "unit_submit",
        "session_submit",
        "practice_exit",
        "vocabulary_open",
        "startup",
        "manual",
    ] = "manual"


class VocabularyUpdate(BaseModel):
    contextual_meaning: str | None = None
    common_meaning: str | None = None
    phonetic: str | None = None
    part_of_speech: str | None = None
    note: str | None = None
    study_status: Literal["learning", "mastered"] | None = None
    manually_frequent: bool | None = None


class VocabularyReview(BaseModel):
    response_projection: Literal["full", "ack"] = "full"
    expected_revision: str | None = Field(default=None, max_length=2048)
    command_id: str | None = Field(default=None, max_length=32, pattern="^[a-f0-9]{32}$")
    rating: Literal["again", "hard", "mastered", "know", "fluent"]
    mode: Literal["scheduled", "reinforcement"] = "scheduled"


class VocabularyEnrichmentRunRequest(BaseModel):
    action: Literal["start", "pause", "resume", "retry", "inventory"]


class LanSyncHandshake(BaseModel):
    categories: list[str] | None = Field(default=None, max_length=5)
    host_id: str = Field(default="", max_length=36)
    pairing_code: str = Field(default="", max_length=128)
    passcode: str = Field(default="", max_length=64)
    pairing_token: str = Field(default="", max_length=256)
    device_id: str = Field(default="", max_length=128)
    device_model: str = Field(default="", max_length=80, pattern=r"^[^\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]*$")
    profile_names: list[str] = Field(default_factory=list, max_length=200)


class LanSyncPullRequest(BaseModel):
    category: str | None = Field(default=None, max_length=32)
    token: str = Field(min_length=1, max_length=128)
    tables: list[str] = Field(default_factory=list, max_length=64)
    cursor: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tombstone_cursor: dict[str, Any] = Field(default_factory=dict)


class LanSyncPushRequest(BaseModel):
    category: str | None = Field(default=None, max_length=32)
    token: str = Field(min_length=1, max_length=128)
    changes: dict[str, list[dict[str, Any]]] = Field(default_factory=dict, max_length=64)
    tombstones: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def validate_payload_bounds(self):
        change_rows = sum(len(rows) for rows in self.changes.values())
        if change_rows > LAN_SYNC_MAX_CHANGE_ROWS:
            raise ValueError(
                f"局域网同步单次最多接收 {LAN_SYNC_MAX_CHANGE_ROWS} 条变更"
            )
        encoded_size = len(
            json.dumps(
                self.model_dump(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if encoded_size > LAN_SYNC_MAX_REQUEST_BYTES:
            raise ValueError("局域网同步单次请求不能超过 16 MiB")
        return self


class SpeechSynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=80)
    accent: Literal["us", "gb"] = "us"
    request_id: str | None = Field(default=None, max_length=80)


class VocabularyRequestRun(BaseModel):
    action: Literal['pause', 'acknowledge', 'continue', 'retry', 'retry-all']
    task: Literal['translation', 'enrichment'] | None = None
    command_id: str | None = Field(default=None, max_length=80)
    entry_ids: list[int] | None = Field(default=None, max_length=1000)
    attempt_id: str | None = Field(default=None, max_length=80)
    confirmed: bool = False

    @field_validator('entry_ids', mode='before')
    @classmethod
    def validate_request_ids(cls, value):
        if value is not None and (not isinstance(value, list) or any(type(item) is not int or item <= 0 or item > 9007199254740991 for item in value)):
            raise ValueError('Invalid vocabulary entry IDs')
        return list(dict.fromkeys(value)) if value is not None else None

    @field_validator('confirmed', mode='before')
    @classmethod
    def validate_confirmation(cls, value):
        if type(value) is not bool:
            raise ValueError('Confirmation must be boolean')
        return value
