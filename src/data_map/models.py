from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, model_validator

class ActionEnum(str, Enum):
    DELETE = "DELETE"
    ANONYMIZE = "ANONYMIZE"
    CRYPTO_SHRED = "CRYPTO_SHRED"
    RETAIN_WITH_REASON = "RETAIN_WITH_REASON"

class AnonymizeStrategyEnum(str, Enum):
    NULLIFY = "NULLIFY"
    RANDOM = "RANDOM"
    GENERALIZE = "GENERALIZE"

class ConnectorTypeEnum(str, Enum):
    POSTGRES = "postgres"
    WEBHOOK = "webhook"
    MANUAL = "manual"

class RetentionPolicy(BaseModel):
    days: int = Field(gt=0, description="Retention duration in days")
    basis: str = Field(min_length=3, description="Legal basis for retention exemption")

class DatasetConfig(BaseModel):
    name: str = Field(min_length=1)
    subject_key: str = Field(min_length=1)
    action: ActionEnum
    anonymize: Optional[Dict[str, str]] = None
    retention: Optional[RetentionPolicy] = None
    on_expiry: Optional[ActionEnum] = None

    @model_validator(mode='after')
    def validate_action_constraints(self) -> 'DatasetConfig':
        if self.action == ActionEnum.ANONYMIZE:
            if not self.anonymize:
                raise ValueError(f"Dataset '{self.name}' configured for ANONYMIZE must specify field anonymization rules")
            for field, strategy in self.anonymize.items():
                upper_strat = strategy.upper()
                if "HASH" in upper_strat or "SHA" in upper_strat or "MD5" in upper_strat or "SALT" in upper_strat:
                    raise ValueError(
                        f"Prohibited anonymization strategy '{strategy}' on field '{field}'. "
                        "Hashing is pseudonymization, not anonymization under GDPR. "
                        "Only NULLIFY, RANDOM, and GENERALIZE are permitted."
                    )
                if upper_strat not in [s.value for s in AnonymizeStrategyEnum]:
                    raise ValueError(
                        f"Unsupported anonymization strategy '{strategy}' on field '{field}'. "
                        f"Allowed: {[s.value for s in AnonymizeStrategyEnum]}"
                    )
        elif self.action == ActionEnum.RETAIN_WITH_REASON:
            if not self.retention:
                raise ValueError(f"Dataset '{self.name}' with RETAIN_WITH_REASON must declare retention days and legal basis")
            if not self.on_expiry:
                self.on_expiry = ActionEnum.DELETE
        return self

class StoreConfig(BaseModel):
    id: str = Field(min_length=1)
    connector: ConnectorTypeEnum
    connection_ref: Optional[str] = None
    datasets: List[DatasetConfig]

class DataMapDocument(BaseModel):
    stores: List[StoreConfig]
