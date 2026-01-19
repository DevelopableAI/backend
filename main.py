from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from models.schemas import (
    ProjectInput, ArchitectureProposal, HumanFeedback,
    GeneratedCode, WorkflowState
)
from services.claude_service import ClaudeService
from agents.schema_analyzer import SchemaAnalyzer
from agents.architecture_proposer import ArchitectureProposer
from agents.code_generator import CodeGenerator
from agents.tests_generator import TestsGenerator
from typing import Dict
import uuid
import os
from dotenv import load_dotenv

from utils.parsers import parse_schema

load_dotenv()

app = FastAPI(
    title="Agentic Backend Generator",
    description="AI-powered backend application generator with human-in-the-loop",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session storage (use Redis in production)
workflow_sessions: Dict[str, WorkflowState] = {}

# Initialize services
claude_service = ClaudeService()
schema_analyzer = SchemaAnalyzer(claude_service)
architecture_proposer = ArchitectureProposer(claude_service)
code_generator = CodeGenerator(claude_service)
tests_generator = TestsGenerator(claude_service)


@app.get("/")
async def root():
    return {
        "message": "Agentic Backend Generator API",
        "version": "1.0.0",
        "endpoints": {
            "parse_schema": "/api/parse-schema (POST) - Parse SQL/JSON/Mongoose schemas",
            "analyze": "/api/analyze",
            "propose": "/api/propose",
            "feedback": "/api/feedback",
            "generate": "/api/generate",
            "status": "/api/status/{session_id}"
        }
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy"}

class SchemaParseRequest(BaseModel):
    """Request to parse schema from raw text"""
    schema_text: str
    format: str = "auto"  # auto, sql, json, mongoose, sqlalchemy
    

@app.post("/api/parse-schema", response_model=dict)
async def parse_schema_endpoint(request: SchemaParseRequest):
    """
    Parse schema from various formats (SQL DDL, JSON, Mongoose, SQLAlchemy)
    This is a helper endpoint to convert raw schemas before analysis
    """
    try:
        # Parse the schema
        parsed_schema = parse_schema(request.schema_text, format=request.format)
        
        if not parsed_schema:
            raise HTTPException(
                status_code=400, 
                detail=f"Failed to parse schema. Please check the format and try again."
            )
        
        # Return the parsed schema in a format compatible with EntitySchema
        return {
            "entity_schema": {
                "entity_name": parsed_schema.entity_name,
                "fields": [
                    {
                        "name": field.name,
                        "type": field.type,
                        "constraints": field.constraints,
                        "description": field.description
                    }
                    for field in parsed_schema.fields
                ],
                "primary_key": parsed_schema.primary_key,
                "indexes": parsed_schema.indexes or [],
                "relationships": parsed_schema.relationships or {}
            },
            "message": f"Successfully parsed {request.format} schema",
            "detected_format": request.format
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error parsing schema: {str(e)}")



@app.post("/api/analyze", response_model=dict)
async def analyze_project(project_input: ProjectInput):
    """
    Step 1: Analyze the entity schema and business requirements
    """
    try:
        # Create new workflow session
        session_id = str(uuid.uuid4())
        
        # Analyze the schema
        analysis = await schema_analyzer.analyze(project_input)
        
        # Create workflow state
        workflow_state = WorkflowState(
            session_id=session_id,
            project_input=project_input,
            current_step="analyzed"
        )
        
        workflow_sessions[session_id] = workflow_state
        
        return {
            "session_id": session_id,
            "analysis": analysis,
            "message": "Schema analyzed successfully. Proceed to architecture proposal."
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/propose/{session_id}", response_model=ArchitectureProposal)
async def propose_architecture(session_id: str):
    """
    Step 2: Propose architecture based on analysis
    This is where the AI suggests what to build
    """
    try:
        if session_id not in workflow_sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        
        workflow_state = workflow_sessions[session_id]
        
        if workflow_state.current_step != "analyzed":
            raise HTTPException(
                status_code=400,
                detail=f"Invalid workflow step. Current: {workflow_state.current_step}"
            )
        
        # Get entity analysis
        analysis = await schema_analyzer.analyze(workflow_state.project_input)
        
        # Generate architecture proposal
        proposal = await architecture_proposer.propose(
            workflow_state.project_input,
            analysis
        )
        
        # Update workflow state
        workflow_state.architecture_proposal = proposal
        workflow_state.current_step = "proposed"
        workflow_sessions[session_id] = workflow_state
        
        return proposal
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/feedback/{session_id}")
async def submit_human_feedback(session_id: str, feedback: HumanFeedback):
    """
    Step 3: Human-in-the-loop - Developer reviews and modifies the proposal
    """
    try:
        if session_id not in workflow_sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        
        workflow_state = workflow_sessions[session_id]
        
        if workflow_state.current_step != "proposed":
            raise HTTPException(
                status_code=400,
                detail=f"Invalid workflow step. Current: {workflow_state.current_step}"
            )
        
        # Store human feedback
        workflow_state.human_feedback = feedback
        workflow_state.current_step = "feedback_received"
        workflow_sessions[session_id] = workflow_state
        
        return {
            "message": "Feedback received. Ready to generate code.",
            "session_id": session_id,
            "approved": feedback.approved
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate/{session_id}", response_model=GeneratedCode)
async def generate_code(session_id: str, background_tasks: BackgroundTasks):
    """
    Step 4: Generate the actual code based on approved architecture
    """
    try:
        if session_id not in workflow_sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        
        workflow_state = workflow_sessions[session_id]
        
        if workflow_state.current_step != "feedback_received":
            raise HTTPException(
                status_code=400,
                detail=f"Invalid workflow step. Current: {workflow_state.current_step}"
            )
        
        if not workflow_state.human_feedback.approved:
            raise HTTPException(
                status_code=400,
                detail="Cannot generate code without approval"
            )
        
        # Generate code
        generated_code = await code_generator.generate(
            workflow_state.project_input,
            workflow_state.architecture_proposal,
            workflow_state.human_feedback
        )
        
        # Update workflow state
        workflow_state.generated_code = generated_code
        workflow_state.current_step = "completed"
        workflow_sessions[session_id] = workflow_state
        
        return generated_code
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/api/generate-tests/{session_id}")
async def generate_tests(session_id: str):
    # Use TestsGenerator agent
    test_files = await tests_generator.generate(...)
    return {"test_files": test_files}


@app.get("/api/status/{session_id}")
async def get_workflow_status(session_id: str):
    """
    Get the current status of a workflow session
    """
    if session_id not in workflow_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    workflow_state = workflow_sessions[session_id]
    
    return {
        "session_id": session_id,
        "current_step": workflow_state.current_step,
        "has_proposal": workflow_state.architecture_proposal is not None,
        "has_feedback": workflow_state.human_feedback is not None,
        "has_code": workflow_state.generated_code is not None
    }


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    """
    Clean up a workflow session
    """
    if session_id in workflow_sessions:
        del workflow_sessions[session_id]
        return {"message": "Session deleted successfully"}
    else:
        raise HTTPException(status_code=404, detail="Session not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)