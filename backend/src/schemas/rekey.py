from pydantic import BaseModel, Field


class KeyInput(BaseModel):
    label: str = Field(min_length=2, max_length=80)
    retain_for_recovery: bool = True


class RekeyRunInput(BaseModel):
    label: str = Field(min_length=2, max_length=120)
    old_key_id: int = Field(gt=0)
    new_key_id: int = Field(gt=0)
    page_count: int = Field(ge=4, le=60, default=12)
    batch_size: int = Field(ge=1, le=20, default=4)
    retire_at_tick: int = Field(ge=1, le=50, default=6)


class PageWriteInput(BaseModel):
    value: str = Field(min_length=1, max_length=500)


class ReplicaInput(BaseModel):
    available: bool | None = None
    fenced: bool | None = None
    vote_enabled: bool | None = None
    new_key_ready: bool | None = None


class FailoverInput(BaseModel):
    fail_primary: bool = True
    fence_primary: bool = True
    max_data_loss: int = Field(ge=0, le=1000000, default=1000)
    required_term: int = Field(ge=1, le=1000000, default=7)
