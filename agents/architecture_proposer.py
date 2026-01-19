from services.claude_service import ClaudeService
from models.schemas import (
    ProjectInput, ArchitectureProposal, ApplicationType, FrameworkType
)
from typing import Dict, Any
import json


class ArchitectureProposer:
    """Agent responsible for proposing architecture and implementation approach"""
    
    def __init__(self, claude_service: ClaudeService):
        self.claude = claude_service
    
    async def propose(
        self,
        project_input: ProjectInput,
        entity_analysis: Dict[str, Any]
    ) -> ArchitectureProposal:
        
        system_prompt = """You are an expert backend architect specializing in REST API design.
        Your job is to propose the optimal architecture for implementing a CRUD REST API.

        Consider:
        1. Framework selection (FastAPI for Python, Express for JavaScript)
        2. Architecture pattern (Layered, Clean Architecture, MVC)
        3. RESTful endpoint design
        4. Optimization strategies (caching, connection pooling, async processing)
        5. Concurrency handling
        6. Security best practices
        7. Error handling and validation

        Be specific and practical in your recommendations."""

        user_message = f"""Based on this analysis, propose an architecture:
        ENTITY ANALYSIS: {entity_analysis['entity_analysis']}

        ENTITY: {entity_analysis['entity_name']}
        COMPLEXITY: {entity_analysis['complexity_level']}
        LANGUAGE: {project_input.preferred_language}
        DATABASE: {project_input.db_type}

        Provide your proposal in the following JSON format:
        {{
        "application_type": "rest_api",
        "framework": "fastapi" or "express",
        "architecture_pattern": "string describing the pattern",
        "suggested_endpoints": [
            {{"method": "POST", "path": "/api/entities", "purpose": "Create entity"}},
            {{"method": "GET", "path": "/api/entities", "purpose": "List all entities"}},
            {{"method": "GET", "path": "/api/entities/{{id}}", "purpose": "Get single entity"}},
            {{"method": "PUT", "path": "/api/entities/{{id}}", "purpose": "Update entity"}},
            {{"method": "DELETE", "path": "/api/entities/{{id}}", "purpose": "Delete entity"}}
        ],
        "optimization_strategies": [
            "List specific optimization strategies like caching, pooling, etc."
        ],
        "rationale": "Detailed explanation of why these choices were made"
        }}"""

        response_schema = {
            "application_type": "string",
            "framework": "string",
            "architecture_pattern": "string",
            "suggested_endpoints": "array",
            "optimization_strategies": "array",
            "rationale": "string"
        }
        
        proposal_data = await self.claude.generate_structured_response(
            system_prompt=system_prompt,
            user_message=user_message,
            response_schema=response_schema,
            temperature=0.5
        )
        
        return ArchitectureProposal(
            application_type=ApplicationType.REST_API,
            framework=FrameworkType(proposal_data["framework"]),
            architecture_pattern=proposal_data["architecture_pattern"],
            suggested_endpoints=proposal_data["suggested_endpoints"],
            optimization_strategies=proposal_data["optimization_strategies"],
            rationale=proposal_data["rationale"]
        )