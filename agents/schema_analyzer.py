from services.claude_service import ClaudeService
from models.schemas import EntitySchema, ProjectInput
from typing import Dict, Any


class SchemaAnalyzer:
    """Agent responsible for analyzing entity schema and business requirements"""
    
    def __init__(self, claude_service: ClaudeService):
        self.claude = claude_service
    
    async def analyze(self, project_input: ProjectInput) -> Dict[str, Any]:
        """
        Analyze the entity schema and business requirements to extract:
        - Entity characteristics
        - Required CRUD operations
        - Validation rules
        - Performance considerations
        """
        
        system_prompt = """You are an expert backend architect analyzing database schemas and business requirements.
        Your job is to deeply understand the entity, its purpose, and how it should be implemented as a REST API.

        Focus on:
        1. Entity characteristics (fields, types, constraints)
        2. Required CRUD operations based on business needs
        3. Validation rules and business logic
        4. Performance and optimization needs
        5. Relationships with other entities
        6. Security considerations"""

        user_message = f"""Analyze this entity and business requirements:
        
        ENTITY SCHEMA:
        Name: {project_input.entity_schema.entity_name}
        Primary Key: {project_input.entity_schema.primary_key}

        Fields:
        {self._format_fields(project_input.entity_schema.fields)}

        Indexes: {project_input.entity_schema.indexes}
        Relationships: {project_input.entity_schema.relationships}

        BUSINESS REQUIREMENTS:
        {project_input.business_requirements}

        DATABASE TYPE: {project_input.db_type}
        PREFERRED LANGUAGE: {project_input.preferred_language}

        Provide a comprehensive analysis of this entity."""

        analysis = await self.claude.generate_response(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.5
        )
        
        return {
            "entity_analysis": analysis,
            "entity_name": project_input.entity_schema.entity_name,
            "complexity_level": self._assess_complexity(project_input.entity_schema)
        }
    
    def _format_fields(self, fields) -> str:
        """Format fields for display"""
        formatted = []
        for field in fields:
            constraints = f" (Constraints: {', '.join(field.constraints)})" if field.constraints else ""
            desc = f" - {field.description}" if field.description else ""
            formatted.append(f"  - {field.name}: {field.type}{constraints}{desc}")
        return "\n".join(formatted)
    
    def _assess_complexity(self, schema: EntitySchema) -> str:
        """Assess complexity based on number of fields, relationships, etc."""
        num_fields = len(schema.fields)
        num_relationships = len(schema.relationships) if schema.relationships else 0
        
        if num_fields <= 5 and num_relationships == 0:
            return "low"
        elif num_fields <= 10 and num_relationships <= 2:
            return "medium"
        else:
            return "high"