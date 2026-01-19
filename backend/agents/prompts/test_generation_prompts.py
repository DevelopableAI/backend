class TestGenerationPrompts:
    
    @staticmethod
    def get_test_planning_system_prompt() -> str:
        return """You are an expert test planning agent for backend applications.
        Your job is to analyze the source files and create a comprehensive test plan.

        Return a JSON structure with test groups and files to generate.

        Example response:
        {
            "test_groups": [
                {
                    "category": "unit_tests",
                    "files": ["tests/test_models.py", "tests/test_services.py"]
                },
                {
                    "category": "integration_tests",
                    "files": ["tests/test_api.py", "tests/test_database.py"]
                }
            ]
        }"""
    
    @staticmethod
    def get_test_planning_user_message(
        entity_name: str,
        framework: str,
        source_files: list
    ) -> str:
        return f"""Create a test plan for a {framework} application for entity: {entity_name}

        Source files generated:
        {chr(10).join(f"- {f}" for f in source_files)}

        Plan should include:
        1. Unit tests for models/schemas
        2. Unit tests for services/business logic
        3. Integration tests for API endpoints
        4. Database integration tests

        Return JSON with test_groups array."""
    
    @staticmethod
    def get_fastapi_test_generation_system_prompt() -> str:
        return """You are an expert Python/FastAPI test generator.

        Generate production-quality pytest test code for the specified test file.
        Use pytest fixtures, async test functions, and proper mocking.

        CRITICAL RULES:
        1. Output ONLY raw Python code - NO markdown, NO explanations, NO JSON
        2. Do NOT wrap in ```python or ``` blocks
        3. Include proper imports (pytest, fastapi.testclient, etc.)
        4. Use pytest fixtures from conftest.py
        5. Write comprehensive test cases with clear assertions
        6. Include both success and error case tests
        7. Use proper async/await patterns for async endpoints
        8. Mock external dependencies appropriately

        Generate complete, production-ready test code."""
    
    
    @staticmethod
    def get_fastapi_test_generation_user_message(
        entity_name: str,
        fields: list,
        db_type: str,
        test_filename: str,
        source_files: list,
        all_test_filenames: list
    ) -> str:
        fields_str = "\n".join([
            f"  - {f['name']}: {f['type']} ({f.get('constraints', 'no constraints')})"
            for f in fields
        ])
        
        return f"""Generate test file: {test_filename}

        Entity: {entity_name}
        Database: {db_type}

        Fields:
        {fields_str}

        Source files available:
        {chr(10).join(f"- {f}" for f in source_files)}

        Other test files in plan:
        {chr(10).join(f"- {f}" for f in all_test_filenames)}

        Generate COMPLETE test code for {test_filename}.
        Output ONLY the Python code - no markdown, no explanations."""

    @staticmethod
    def get_express_test_generation_system_prompt() -> str:
        return """You are an expert Node.js/Express test generator.

        Generate production-quality Jest/Supertest test code for the specified test file.

        CRITICAL RULES:
        1. Output ONLY raw JavaScript code - NO markdown, NO explanations, NO JSON
        2. Do NOT wrap in ```javascript or ``` blocks
        3. Include proper imports (jest, supertest, etc.)
        4. Use proper test structure with describe/it blocks
        5. Write comprehensive test cases with clear assertions
        6. Include both success and error case tests
        7. Use proper async/await patterns
        8. Mock external dependencies appropriately
        9. Set up and tear down test database properly

        Generate complete, production-ready test code."""
    
    @staticmethod
    def get_express_test_generation_user_message(
        entity_name: str,
        fields: list,
        db_type: str,
        test_filename: str,
        source_files: list,
        all_test_filenames: list
    ) -> str:
        fields_str = "\n".join([
            f"  - {f['name']}: {f['type']} ({f.get('constraints', 'no constraints')})"
            for f in fields
        ])
        
        return f"""Generate test file: {test_filename}

        Entity: {entity_name}
        Database: {db_type}

        Fields:
        {fields_str}

        Source files available:
        {chr(10).join(f"- {f}" for f in source_files)}

        Other test files in plan:
        {chr(10).join(f"- {f}" for f in all_test_filenames)}

        Generate COMPLETE test code for {test_filename}.
        Output ONLY the JavaScript code - no markdown, no explanations."""
    
    @staticmethod
    def get_pytest_config_system_prompt() -> str:
        return """Generate a pytest.ini configuration file.
        Output ONLY the raw INI content - NO markdown, NO explanations."""
    
    @staticmethod
    def get_pytest_config_user_message(entity_name: str) -> str:
        return f"""Generate pytest.ini for {entity_name} API tests.

        Include:
        - Test discovery paths
        - Async support
        - Coverage settings
        - Logging configuration

        Output ONLY the INI content."""
    
    @staticmethod
    def get_conftest_system_prompt() -> str:
        return """Generate a pytest conftest.py with fixtures.
        Output ONLY raw Python code - NO markdown, NO explanations."""

    @staticmethod
    def get_conftest_user_message(entity_name: str, db_type: str) -> str:
        return f"""Generate conftest.py for {entity_name} API tests using {db_type}.

        Include fixtures for:
        - Test database setup/teardown
        - Test client (FastAPI TestClient)
        - Sample test data
        - Authentication tokens if needed

        Output ONLY the Python code."""
    
    @staticmethod
    def get_jest_config_system_prompt() -> str:
        return """Generate a jest.config.js configuration file.
        Output ONLY raw JavaScript code - NO markdown, NO explanations."""

    @staticmethod
    def get_jest_config_user_message(entity_name: str) -> str:
        return f"""Generate jest.config.js for {entity_name} API tests.

        Include:
        - Test environment (node)
        - Coverage settings
        - Test match patterns
        - Setup files

        Output ONLY the JavaScript code."""