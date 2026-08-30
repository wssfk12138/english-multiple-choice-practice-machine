from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PracticeCreate(BaseModel):
    mode: Literal["paper", "unit", "random", "wrong"]
    paper_id: int | None = None
    unit_ids: list[int] = Field(default_factory=list)
    question_ids: list[int] = Field(default_factory=list)
    unit_type: str | None = None
    selection_scope: Literal["unit", "paper_unit_type"] = "unit"
    count: int = 1
    shuffle_options: bool = True


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
    base_url: str
    api_key: str | None = None
    model: str
    temperature: float = 0.2
    max_tokens: int = Field(default=0, ge=0)
    system_prompt: str = ""


class AiModelListRequest(BaseModel):
    base_url: str
    api_key: str | None = None
    use_saved_api_key: bool = False
    profile_id: int | None = None


class AiProfileWrite(BaseModel):
    name: str = "本地模型"
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key: str | None = None
    clear_api_key: bool = False
    enabled: bool = True
    is_default: bool = False
    default_model: str = ""
    temperature: float = 0.2
    max_tokens: int = Field(default=0, ge=0)
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
    rating: Literal["again", "hard", "mastered"]
