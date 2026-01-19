from services.claude_service import ClaudeService
from models.schemas import (
    ProjectInput, ArchitectureProposal, HumanFeedback, GeneratedCode, FrameworkType
)
from agents.prompts import TestGenerationPrompts
from typing import Dict, Any, List
import json


class TestsGenerator:
    
    def __init__(self, claude_service: ClaudeService):
        self.claude = claude_service
        self.prompts = TestGenerationPrompts()
    
    async def generate(
        self,
        project_input: ProjectInput,
        architecture_proposal: ArchitectureProposal,
        human_feedback: HumanFeedback,
        generated_code: GeneratedCode
    ) -> Dict[str, str]:
        """Generate test files for the generated code"""
        
        final_framework = human_feedback.framework or architecture_proposal.framework
        
        if final_framework == FrameworkType.FASTAPI:
            return await self._generate_fastapi_tests(
                project_input,
                architecture_proposal,
                human_feedback,
                generated_code
            )
        elif final_framework == FrameworkType.EXPRESS:
            return await self._generate_express_tests(
                project_input,
                architecture_proposal,
                human_feedback,
                generated_code
            )
        else:
            raise ValueError(f"Unsupported framework: {final_framework}")
    
    async def _generate_fastapi_tests(
        self,
        project_input: ProjectInput,
        architecture_proposal: ArchitectureProposal,
        human_feedback: HumanFeedback,
        generated_code: GeneratedCode
    ) -> Dict[str, str]:
        
        entity_name = project_input.entity_schema.entity_name
        
        # Step 1: Create test plan
        test_plan = await self._create_test_plan(
            entity_name,
            "fastapi",
            list(generated_code.files.keys())
        )
        
        # Flatten test file list
        all_test_filenames = []
        for group in test_plan.get('test_groups', []):
            all_test_filenames.extend(group.get('files', []))
        
        # Step 2: Generate test files one by one
        test_files = {}
        
        for test_filename in all_test_filenames:
            print(f"Generating test: {test_filename}...")
            
            test_code = await self._generate_single_test_file_fastapi(
                project_input,
                architecture_proposal,
                human_feedback,
                test_filename,
                generated_code.files,
                all_test_filenames
            )
            
            if test_code:
                test_files[test_filename] = test_code
            else:
                print(f"Warning: {test_filename} generation returned empty")
        
        # Step 3: Generate test configuration files
        pytest_ini = await self._generate_pytest_config(entity_name)
        if pytest_ini:
            test_files["pytest.ini"] = pytest_ini
        
        conftest = await self._generate_conftest(entity_name, project_input.db_type)
        if conftest:
            test_files["tests/conftest.py"] = conftest
        
        return test_files
    
    async def _generate_express_tests(
        self,
        project_input: ProjectInput,
        architecture_proposal: ArchitectureProposal,
        human_feedback: HumanFeedback,
        generated_code: GeneratedCode
    ) -> Dict[str, str]:
        
        entity_name = project_input.entity_schema.entity_name
        
        # Step 1: Create test plan
        test_plan = await self._create_test_plan(
            entity_name,
            "express",
            list(generated_code.files.keys())
        )
        
        # Flatten test file list
        all_test_filenames = []
        for group in test_plan.get('test_groups', []):
            all_test_filenames.extend(group.get('files', []))
        
        # Step 2: Generate test files one by one
        test_files = {}
        
        for test_filename in all_test_filenames:
            print(f"Generating test: {test_filename}...")
            
            test_code = await self._generate_single_test_file_express(
                project_input,
                architecture_proposal,
                human_feedback,
                test_filename,
                generated_code.files,
                all_test_filenames
            )
            
            if test_code:
                test_files[test_filename] = test_code
            else:
                print(f"Warning: {test_filename} generation returned empty")
        
        # Step 3: Generate test configuration
        jest_config = await self._generate_jest_config(entity_name)
        if jest_config:
            test_files["jest.config.js"] = jest_config
        
        return test_files
    
    async def _create_test_plan(
        self,
        entity_name: str,
        framework: str,
        source_files: List[str]
    ) -> Dict[str, Any]:
        
        system_prompt = self.prompts.get_test_planning_system_prompt()
        user_message = self.prompts.get_test_planning_user_message(
            entity_name,
            framework,
            source_files
        )
        
        response = await self.claude.generate_structured_response(
            system_prompt=system_prompt,
            user_message=user_message,
            response_schema={},
            temperature=0.5
        )
        
        return response
    
    async def _generate_single_test_file_fastapi(
        self,
        project_input: ProjectInput,
        architecture_proposal: ArchitectureProposal,
        human_feedback: HumanFeedback,
        test_filename: str,
        source_files: Dict[str, str],
        all_test_filenames: List[str]
    ) -> str:
        """Generate a single test file - returns raw code"""
        
        fields = [
            {
                "name": f.name,
                "type": f.type,
                "constraints": f.constraints,
                "description": f.description
            }
            for f in project_input.entity_schema.fields
        ]
        
        system_prompt = self.prompts.get_fastapi_test_generation_system_prompt()
        user_message = self.prompts.get_fastapi_test_generation_user_message(
            entity_name=project_input.entity_schema.entity_name,
            fields=fields,
            db_type=project_input.db_type,
            test_filename=test_filename,
            source_files=list(source_files.keys()),
            all_test_filenames=all_test_filenames
        )
        
        code = await self.claude.generate_response(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.1,
            max_tokens=8192
        )
        
        # Clean up markdown
        code = code.strip()
        if code.startswith("```python") or code.startswith("```py"):
            code = code.split("\n", 1)[1]
        if code.startswith("```"):
            code = code.split("\n", 1)[1]
        if code.endswith("```"):
            code = code.rsplit("\n", 1)[0]
        
        return code.strip()
    
    async def _generate_single_test_file_express(
        self,
        project_input: ProjectInput,
        architecture_proposal: ArchitectureProposal,
        human_feedback: HumanFeedback,
        test_filename: str,
        source_files: Dict[str, str],
        all_test_filenames: List[str]
    ) -> str:
        """Generate a single test file - returns raw code"""
        
        fields = [
            {
                "name": f.name,
                "type": f.type,
                "constraints": f.constraints,
                "description": f.description
            }
            for f in project_input.entity_schema.fields
        ]
        
        system_prompt = self.prompts.get_express_test_generation_system_prompt()
        user_message = self.prompts.get_express_test_generation_user_message(
            entity_name=project_input.entity_schema.entity_name,
            fields=fields,
            db_type=project_input.db_type,
            test_filename=test_filename,
            source_files=list(source_files.keys()),
            all_test_filenames=all_test_filenames
        )
        
        code = await self.claude.generate_response(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.1,
            max_tokens=8192
        )
        
        # Clean up markdown
        code = code.strip()
        if code.startswith("```javascript") or code.startswith("```js"):
            code = code.split("\n", 1)[1]
        if code.startswith("```"):
            code = code.split("\n", 1)[1]
        if code.endswith("```"):
            code = code.rsplit("\n", 1)[0]
        
        return code.strip()
    
    async def _generate_pytest_config(self, entity_name: str) -> str:
        """Generate pytest.ini configuration"""
        
        system_prompt = self.prompts.get_pytest_config_system_prompt()
        user_message = self.prompts.get_pytest_config_user_message(entity_name)
        
        try:
            config = await self.claude.generate_response(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
                max_tokens=1024
            )
            
            # Clean up markdown
            config = config.strip()
            if config.startswith("```ini") or config.startswith("```"):
                config = config.split("\n", 1)[1]
            if config.endswith("```"):
                config = config.rsplit("\n", 1)[0]
            
            return config.strip()
        except Exception as e:
            print(f"Warning: Failed to generate pytest config: {e}")
            return ""
    
    async def _generate_conftest(self, entity_name: str, db_type: str) -> str:
        """Generate pytest conftest.py with fixtures"""
        
        system_prompt = self.prompts.get_conftest_system_prompt()
        user_message = self.prompts.get_conftest_user_message(entity_name, db_type)
        
        try:
            config = await self.claude.generate_response(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
                max_tokens=4096
            )
            
            # Clean up markdown
            config = config.strip()
            if config.startswith("```python") or config.startswith("```py"):
                config = config.split("\n", 1)[1]
            if config.startswith("```"):
                config = config.split("\n", 1)[1]
            if config.endswith("```"):
                config = config.rsplit("\n", 1)[0]
            
            return config.strip()
        except Exception as e:
            print(f"Warning: Failed to generate conftest: {e}")
            return ""
    
    async def _generate_jest_config(self, entity_name: str) -> str:
        """Generate jest.config.js configuration"""
        
        system_prompt = self.prompts.get_jest_config_system_prompt()
        user_message = self.prompts.get_jest_config_user_message(entity_name)
        
        try:
            config = await self.claude.generate_response(
                system_prompt=system_prompt,
                user_message=user_message,
                temperature=0.1,
                max_tokens=1024
            )
            
            # Clean up markdown
            config = config.strip()
            if config.startswith("```javascript") or config.startswith("```js"):
                config = config.split("\n", 1)[1]
            if config.startswith("```"):
                config = config.split("\n", 1)[1]
            if config.endswith("```"):
                config = config.rsplit("\n", 1)[0]
            
            return config.strip()
        except Exception as e:
            print(f"Warning: Failed to generate jest config: {e}")
            return ""