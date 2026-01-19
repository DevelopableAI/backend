from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Literal
from enum import Enum


class FrameworkType(str, Enum):
    FASTAPI = "fastapi"
    EXPRESS = "express"


class ApplicationType(str, Enum):
    REST_API = "rest_api"
    BATCH_JOB = "batch_job"
    EVENT_DRIVEN = "event_driven"


class FieldSchema(BaseModel):
    name: str
    type: str
    constraints: Optional[List[str]] = []
    description: Optional[str] = None


class EntitySchema(BaseModel):
    entity_name: str
    fields: List[FieldSchema]
    primary_key: str
    indexes: Optional[List[str]] = []
    relationships: Optional[Dict[str, str]] = {}


class ProjectInput(BaseModel):
    entity_schema: EntitySchema
    business_requirements: str
    preferred_language: Literal["python", "javascript"]
    db_type: Literal["postgresql", "mongodb", "mysql"] = "postgresql"


class ArchitectureProposal(BaseModel):
    application_type: ApplicationType
    framework: FrameworkType
    architecture_pattern: str
    suggested_endpoints: List[Dict[str, str]]
    optimization_strategies: List[str]
    rationale: str


class HumanFeedback(BaseModel):
    approved: bool
    application_type: Optional[ApplicationType] = None
    framework: Optional[FrameworkType] = None
    custom_requirements: Optional[str] = None


class GeneratedCode(BaseModel):
    framework: str
    files: Dict[str, str]
    setup_instructions: str
    dependencies: List[str]


class WorkflowState(BaseModel):
    project_input: Optional[ProjectInput] = None
    architecture_proposal: Optional[ArchitectureProposal] = None
    human_feedback: Optional[HumanFeedback] = None
    generated_code: Optional[GeneratedCode] = None
    current_step: str = "input"
    session_id: str