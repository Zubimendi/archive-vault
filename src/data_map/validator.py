import yaml
import json
from typing import Union, Dict, Any
from .models import DataMapDocument

class DataMapValidator:
    """Parses and validates declarative Data Map documents (YAML or JSON)."""

    @classmethod
    def parse_and_validate(cls, raw_content: Union[str, Dict[str, Any]]) -> DataMapDocument:
        if isinstance(raw_content, str):
            try:
                # Attempt JSON first, then YAML
                data = json.loads(raw_content)
            except json.JSONDecodeError:
                try:
                    data = yaml.safe_load(raw_content)
                except Exception as e:
                    raise ValueError(f"Failed to parse data map format: {str(e)}")
        elif isinstance(raw_content, dict):
            data = raw_content
        else:
            raise ValueError("Data map content must be string or dict")
        
        # Validates through Pydantic model and validators
        return DataMapDocument.model_validate(data)
