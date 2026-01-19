from services.claude_service import ClaudeService
from models.schemas import (
    ProjectInput, ArchitectureProposal, HumanFeedback, GeneratedCode, FrameworkType
)
from agents.prompts import CodeGenerationPrompts
from typing import Dict, Any, List
import json


class CodeGenerator:
    
    def __init__(self, claude_service: ClaudeService):
        self.claude = claude_service
        self.prompts = CodeGenerationPrompts()
    
    async def generate(
        self,
        project_input: ProjectInput,
        architecture_proposal: ArchitectureProposal,
        human_feedback: HumanFeedback
    ) -> GeneratedCode:
        
        final_framework = human_feedback.framework or architecture_proposal.framework
        
        if final_framework == FrameworkType.FASTAPI:
            return await self._generate_fastapi_code(project_input, architecture_proposal, human_feedback)
        elif final_framework == FrameworkType.EXPRESS:
            return await self._generate_express_code(project_input, architecture_proposal, human_feedback)
        else:
            raise ValueError(f"Unsupported framework: {final_framework}")
    
    async def _generate_fastapi_code(
        self,
        project_input: ProjectInput,
        architecture_proposal: ArchitectureProposal,
        human_feedback: HumanFeedback
    ) -> GeneratedCode:
        
        entity_name = project_input.entity_schema.entity_name
        field_count = len(project_input.entity_schema.fields)
        
        # Step 1: Create generation plan
        plan = await self._create_generation_plan(
            entity_name,
            field_count,
            "fastapi",
            architecture_proposal.architecture_pattern
        )
        
        # Flatten file list
        all_filenames = []
        for group in plan['file_groups']:
            all_filenames.extend(group['files'])
        
        # Step 2: Generate files one by one
        all_files = {}
        
        for filename in all_filenames:
            print(f"Generating {filename}...")
            
            code = await self._generate_single_file_fastapi(
                project_input,
                architecture_proposal,
                human_feedback,
                filename,
                all_filenames
            )
            
            if code:
                all_files[filename] = code
            else:
                print(f"Warning: {filename} generation returned empty")
        
        # Step 3: Generate Dockerfile
        print("Generating Dockerfile...")
        dockerfile = await self._generate_dockerfile_fastapi(
            entity_name,
            project_input.db_type,
            list(all_files.keys())
        )
        if dockerfile:
            all_files["Dockerfile"] = dockerfile
        
        # Step 4: Generate docker-compose.yml
        print("Generating docker-compose.yml...")
        docker_compose = await self._generate_docker_compose_fastapi(
            entity_name,
            project_input.db_type
        )
        if docker_compose:
            all_files["docker-compose.yml"] = docker_compose
        
        # Step 5: Generate .dockerignore
        print("Generating .dockerignore...")
        dockerignore = await self._generate_dockerignore("fastapi")
        if dockerignore:
            all_files[".dockerignore"] = dockerignore
        
        # Step 6: Generate dependencies and setup
        dependencies = await self._generate_dependencies("fastapi", list(all_files.keys()))
        setup_instructions = await self._generate_setup_instructions(
            "fastapi",
            entity_name,
            project_input.db_type
        )
        
        return GeneratedCode(
            framework="fastapi",
            files=all_files,
            setup_instructions=setup_instructions,
            dependencies=dependencies
        )
    
    async def _generate_express_code(
        self,
        project_input: ProjectInput,
        architecture_proposal: ArchitectureProposal,
        human_feedback: HumanFeedback
    ) -> GeneratedCode:
        
        entity_name = project_input.entity_schema.entity_name
        field_count = len(project_input.entity_schema.fields)
        
        plan = await self._create_generation_plan(
            entity_name,
            field_count,
            "express",
            architecture_proposal.architecture_pattern
        )
        
        all_filenames = []
        for group in plan['file_groups']:
            all_filenames.extend(group['files'])
        
        all_files = {}
        
        for filename in all_filenames:
            print(f"Generating {filename}...")
            
            code = await self._generate_single_file_express(
                project_input,
                architecture_proposal,
                human_feedback,
                filename,
                all_filenames
            )
            
            if code:
                all_files[filename] = code
            else:
                print(f"Warning: {filename} generation returned empty")
        
        # Step 3: Generate Dockerfile
        print("Generating Dockerfile...")
        dockerfile = await self._generate_dockerfile_express(
            entity_name,
            project_input.db_type,
            list(all_files.keys())
        )
        if dockerfile:
            all_files["Dockerfile"] = dockerfile
        
        # Step 4: Generate docker-compose.yml
        print("Generating docker-compose.yml...")
        docker_compose = await self._generate_docker_compose_express(
            entity_name,
            project_input.db_type
        )
        if docker_compose:
            all_files["docker-compose.yml"] = docker_compose
        
        # Step 5: Generate .dockerignore
        print("Generating .dockerignore...")
        dockerignore = await self._generate_dockerignore("express")
        if dockerignore:
            all_files[".dockerignore"] = dockerignore
        
        # Step 6: Generate dependencies and setup
        dependencies = await self._generate_dependencies("express", list(all_files.keys()))
        setup_instructions = await self._generate_setup_instructions(
            "express",
            entity_name,
            project_input.db_type
        )
        
        return GeneratedCode(
            framework="express",
            files=all_files,
            setup_instructions=setup_instructions,
            dependencies=dependencies
        )
    
    async def _create_generation_plan(
        self,
        entity_name: str,
        field_count: int,
        framework: str,
        architecture: str
    ) -> Dict[str, Any]:
        
        system_prompt = self.prompts.get_planning_system_prompt()
        user_message = self.prompts.get_planning_user_message(
            entity_name,
            field_count,
            framework,
            architecture
        )
        
        response = await self.claude.generate_structured_response(
            system_prompt=system_prompt,
            user_message=user_message,
            response_schema={},
            temperature=0.5
        )
        
        return response
    
    async def _generate_single_file_fastapi(
        self,
        project_input: ProjectInput,
        architecture_proposal: ArchitectureProposal,
        human_feedback: HumanFeedback,
        filename: str,
        all_filenames: List[str]
    ) -> str:
        """Generate a single file - returns raw code"""
        
        fields = [
            {
                "name": f.name,
                "type": f.type,
                "constraints": f.constraints,
                "description": f.description
            }
            for f in project_input.entity_schema.fields
        ]
        
        system_prompt = self.prompts.get_fastapi_file_generation_system_prompt()
        user_message = self.prompts.get_fastapi_file_generation_user_message(
            entity_name=project_input.entity_schema.entity_name,
            fields=fields,
            db_type=project_input.db_type,
            filename=filename,
            all_filenames=all_filenames,
            optimization_strategies=json.dumps(architecture_proposal.optimization_strategies),
            custom_requirements=human_feedback.custom_requirements
        )
        
        code = await self.claude.generate_response(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.1,
            max_tokens=8192
        )
        
        # Clean up any markdown that might have snuck in
        code = code.strip()
        if code.startswith("```python") or code.startswith("```py"):
            code = code.split("\n", 1)[1]
        if code.startswith("```"):
            code = code.split("\n", 1)[1]
        if code.endswith("```"):
            code = code.rsplit("\n", 1)[0]
        
        return code.strip()
    
    async def _generate_single_file_express(
        self,
        project_input: ProjectInput,
        architecture_proposal: ArchitectureProposal,
        human_feedback: HumanFeedback,
        filename: str,
        all_filenames: List[str]
    ) -> str:
        """Generate a single file - returns raw code"""
        
        fields = [
            {
                "name": f.name,
                "type": f.type,
                "constraints": f.constraints,
                "description": f.description
            }
            for f in project_input.entity_schema.fields
        ]
        
        system_prompt = self.prompts.get_express_file_generation_system_prompt()
        user_message = self.prompts.get_express_file_generation_user_message(
            entity_name=project_input.entity_schema.entity_name,
            fields=fields,
            db_type=project_input.db_type,
            filename=filename,
            all_filenames=all_filenames,
            optimization_strategies=json.dumps(architecture_proposal.optimization_strategies),
            custom_requirements=human_feedback.custom_requirements
        )
        
        code = await self.claude.generate_response(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.1,
            max_tokens=4096
        )
        
        # Clean up any markdown
        code = code.strip()
        if code.startswith("```javascript") or code.startswith("```js"):
            code = code.split("\n", 1)[1]
        if code.startswith("```"):
            code = code.split("\n", 1)[1]
        if code.endswith("```"):
            code = code.rsplit("\n", 1)[0]
        
        return code.strip()
    
    async def _generate_dockerfile_fastapi(
        self,
        entity_name: str,
        db_type: str,
        generated_files: List[str]
    ) -> str:
        """Generate Dockerfile for FastAPI application"""
        
        system_prompt = self.prompts.get_dockerfile_system_prompt()
        user_message = self.prompts.get_dockerfile_user_message(
            framework="fastapi",
            entity_name=entity_name,
            db_type=db_type,
            generated_files=generated_files
        )
        
        try:
            dockerfile = await self.claude.generate_response(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
                max_tokens=2048
            )
            
            # Clean up markdown
            dockerfile = dockerfile.strip()
            if dockerfile.startswith("```dockerfile") or dockerfile.startswith("```Dockerfile"):
                dockerfile = dockerfile.split("\n", 1)[1]
            if dockerfile.startswith("```"):
                dockerfile = dockerfile.split("\n", 1)[1]
            if dockerfile.endswith("```"):
                dockerfile = dockerfile.rsplit("\n", 1)[0]
            
            return dockerfile.strip()
        except Exception as e:
            print(f"Warning: Failed to generate Dockerfile: {e}")
            return ""
    
    async def _generate_dockerfile_express(
        self,
        entity_name: str,
        db_type: str,
        generated_files: List[str]
    ) -> str:
        """Generate Dockerfile for Express application"""
        
        system_prompt = self.prompts.get_dockerfile_system_prompt()
        user_message = self.prompts.get_dockerfile_user_message(
            framework="express",
            entity_name=entity_name,
            db_type=db_type,
            generated_files=generated_files
        )
        
        try:
            dockerfile = await self.claude.generate_response(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
                max_tokens=2048
            )
            
            # Clean up markdown
            dockerfile = dockerfile.strip()
            if dockerfile.startswith("```dockerfile") or dockerfile.startswith("```Dockerfile"):
                dockerfile = dockerfile.split("\n", 1)[1]
            if dockerfile.startswith("```"):
                dockerfile = dockerfile.split("\n", 1)[1]
            if dockerfile.endswith("```"):
                dockerfile = dockerfile.rsplit("\n", 1)[0]
            
            return dockerfile.strip()
        except Exception as e:
            print(f"Warning: Failed to generate Dockerfile: {e}")
            return ""
    
    async def _generate_docker_compose_fastapi(
        self,
        entity_name: str,
        db_type: str
    ) -> str:
        """Generate docker-compose.yml for FastAPI application"""
        
        system_prompt = self.prompts.get_docker_compose_system_prompt()
        user_message = self.prompts.get_docker_compose_user_message(
            framework="fastapi",
            entity_name=entity_name,
            db_type=db_type
        )
        
        try:
            compose = await self.claude.generate_response(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
                max_tokens=2048
            )
            
            # Clean up markdown
            compose = compose.strip()
            if compose.startswith("```yaml") or compose.startswith("```yml"):
                compose = compose.split("\n", 1)[1]
            if compose.startswith("```"):
                compose = compose.split("\n", 1)[1]
            if compose.endswith("```"):
                compose = compose.rsplit("\n", 1)[0]
            
            return compose.strip()
        except Exception as e:
            print(f"Warning: Failed to generate docker-compose.yml: {e}")
            return ""
    
    async def _generate_docker_compose_express(
        self,
        entity_name: str,
        db_type: str
    ) -> str:
        """Generate docker-compose.yml for Express application"""
        
        system_prompt = self.prompts.get_docker_compose_system_prompt()
        user_message = self.prompts.get_docker_compose_user_message(
            framework="express",
            entity_name=entity_name,
            db_type=db_type
        )
        
        try:
            compose = await self.claude.generate_response(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
                max_tokens=2048
            )
            
            # Clean up markdown
            compose = compose.strip()
            if compose.startswith("```yaml") or compose.startswith("```yml"):
                compose = compose.split("\n", 1)[1]
            if compose.startswith("```"):
                compose = compose.split("\n", 1)[1]
            if compose.endswith("```"):
                compose = compose.rsplit("\n", 1)[0]
            
            return compose.strip()
        except Exception as e:
            print(f"Warning: Failed to generate docker-compose.yml: {e}")
            return ""
    
    async def _generate_dockerignore(self, framework: str) -> str:
        """Generate .dockerignore file"""
        
        system_prompt = self.prompts.get_dockerignore_system_prompt()
        user_message = self.prompts.get_dockerignore_user_message(framework)
        
        try:
            dockerignore = await self.claude.generate_response(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
                max_tokens=1024
            )
            
            # Clean up markdown
            dockerignore = dockerignore.strip()
            if dockerignore.startswith("```"):
                dockerignore = dockerignore.split("\n", 1)[1]
            if dockerignore.endswith("```"):
                dockerignore = dockerignore.rsplit("\n", 1)[0]
            
            return dockerignore.strip()
        except Exception as e:
            print(f"Warning: Failed to generate .dockerignore: {e}")
            return ""
    
    async def _generate_dependencies(
        self,
        framework: str,
        files_generated: List[str]
    ) -> List[str]:
        
        system_prompt = self.prompts.get_dependencies_system_prompt()
        user_message = self.prompts.get_dependencies_user_message(framework, files_generated)
        
        try:
            response = await self.claude.generate_structured_response(
                system_prompt=system_prompt,
                user_message=user_message,
                response_schema={},
                temperature=0.3
            )
            
            deps = response.get('dependencies', [])
            dev_deps = response.get('dev_dependencies', [])
            return deps + dev_deps
            
        except Exception as e:
            print(f"Warning: Failed to generate dependencies: {e}")
            if framework == "fastapi":
                return ["fastapi", "uvicorn", "sqlalchemy", "pydantic", "python-dotenv"]
            else:
                return ["express", "sequelize", "joi", "dotenv", "morgan"]
    
    async def _generate_setup_instructions(
        self,
        framework: str,
        entity_name: str,
        db_type: str
    ) -> str:
        
        system_prompt = self.prompts.get_setup_instructions_system_prompt()
        user_message = self.prompts.get_setup_instructions_user_message(
            framework,
            entity_name,
            db_type
        )
        
        try:
            instructions = await self.claude.generate_response(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.5,
                max_tokens=2048
            )
            return instructions
        except Exception as e:
            print(f"Warning: Failed to generate setup instructions: {e}")
            return f"# Setup Instructions\n\n1. Install dependencies\n2. Configure database\n3. Run the application"