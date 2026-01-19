class CodeGenerationPrompts:
    
    @staticmethod
    def get_planning_system_prompt():
        return """You are an expert software architect planning code generation.
        Your job is to create a detailed plan for generating a complete REST API application.

        Output ONLY valid JSON with this structure:
        {
        "file_groups": [
            {
            "group_name": "Core Setup",
            "files": ["main.py", "requirements.txt"],
            "description": "App initialization and dependencies"
            },
            {
            "group_name": "Data Models",
            "files": ["models/entity.py", "models/database.py"],
            "description": "Pydantic and database models"
            }
        ],
        "total_files": 10,
        "estimated_iterations": 5
        }"""

    @staticmethod
    def get_planning_user_message(entity_name, field_count, framework, architecture):
        return f"""Plan code generation for this REST API:

        Entity: {entity_name}
        Fields: {field_count}
        Framework: {framework}
        Architecture: {architecture}

        Create a logical grouping of files into 2-3 files per group.
        Group related files together (models together, routes together, etc.)"""

    @staticmethod
    def get_fastapi_file_generation_system_prompt():
        return """You are a code generation engine. Your ONLY job is to output valid Python code.

        Rules:
        1. Output ONLY code - no explanations, no markdown, no comments about the code
        2. Start immediately with the first line of code (import statements or class/function definitions)
        3. Generate complete, production-ready code
        4. Include all necessary imports
        5. Add docstrings and inline comments within the code
        6. Use proper formatting and indentation

        Your response must be valid Python that can be directly saved to a .py file."""

    @staticmethod
    def get_fastapi_file_generation_user_message(
        entity_name,
        fields,
        db_type,
        filename,
        all_filenames,
        optimization_strategies,
        custom_requirements
    ):
        fields_str = "\n".join([
            f"  - {f['name']}: {f['type']} {f.get('constraints', [])}"
            for f in fields
        ])
        
        file_guidance = {
            "main.py": "FastAPI app initialization, middleware, database connection, health check, and all CRUD endpoints",
            "requirements.txt": "List all required packages with versions",
            "config.py": "Settings class with database config, using pydantic-settings",
            ".env.example": "Example environment variables",
            "README.md": "Setup instructions and API documentation"
        }
        
        guidance = file_guidance.get(filename, f"Implementation for {filename}")
        
        return f"""Generate complete code for: {filename}

        Purpose: {guidance}

        Entity: {entity_name}
        Database: {db_type}

        Fields:
        {fields_str}

        Other files in project: {', '.join(all_filenames)}

        Optimizations: {optimization_strategies}
        Custom: {custom_requirements or 'None'}

        Output the complete code starting from the first line. No markdown, no explanations."""

    @staticmethod
    def get_express_file_generation_system_prompt():
        return """You are a code generation engine. Your ONLY job is to output valid JavaScript/Node.js code.

        Rules:
        1. Output ONLY code - no explanations, no markdown, no comments about the code
        2. Start immediately with the first line of code (require/import statements or declarations)
        3. Generate complete, production-ready code
        4. Include all necessary imports
        5. Add JSDoc comments within the code
        6. Use proper formatting and indentation

        Your response must be valid JavaScript that can be directly saved to a .js file."""

    @staticmethod
    def get_express_file_generation_user_message(
        entity_name,
        fields,
        db_type,
        filename,
        all_filenames,
        optimization_strategies,
        custom_requirements
    ):
        fields_str = "\n".join([
            f"  - {f['name']}: {f['type']} {f.get('constraints', [])}"
            for f in fields
        ])
        
        file_guidance = {
            "server.js": "Express app initialization, middleware, database connection, health check, and all CRUD routes",
            "package.json": "Package configuration with all dependencies",
            ".env.example": "Example environment variables",
            "README.md": "Setup instructions and API documentation"
        }
        
        guidance = file_guidance.get(filename, f"Implementation for {filename}")
        
        return f"""Generate complete code for: {filename}

        Purpose: {guidance}

        Entity: {entity_name}
        Database: {db_type}

        Fields:
        {fields_str}

        Other files in project: {', '.join(all_filenames)}

        Optimizations: {optimization_strategies}
        Custom: {custom_requirements or 'None'}

        Output the complete code starting from the first line. No markdown, no explanations."""

    @staticmethod
    def get_dependencies_system_prompt():
        return """You are a dependency management expert.
        List all required dependencies for the generated code.

        Output ONLY valid JSON:
        {
        "dependencies": ["package1", "package2"],
        "dev_dependencies": ["dev-package1"]
        }"""

    @staticmethod
    def get_dependencies_user_message(framework, files_generated):
        return f"""List all dependencies needed for this {framework} application.

        Generated files include:
        {', '.join(files_generated)}

        Provide complete list of runtime and dev dependencies."""

    @staticmethod
    def get_setup_instructions_system_prompt():
        return """You are a technical writer creating setup instructions.
        Write clear, step-by-step instructions for running the generated application."""

    @staticmethod
    def get_setup_instructions_user_message(framework, entity_name, db_type):
        return f"""Write setup instructions for:

        Framework: {framework}
        Entity: {entity_name}
        Database: {db_type}

        Include:
        1. Environment setup
        2. Dependency installation
        3. Database configuration
        4. Running the application
        5. Testing the API"""
            
    @staticmethod
    def get_dockerfile_system_prompt() -> str:
        return """You are an expert DevOps engineer specializing in Docker containerization.

        Generate a production-ready Dockerfile for the application.

        CRITICAL RULES:
        1. Output ONLY raw Dockerfile content - NO markdown, NO explanations
        2. Do NOT wrap in ```dockerfile or ``` blocks
        3. Use multi-stage builds when appropriate
        4. Follow Docker best practices:
        - Use specific base image versions
        - Minimize layers
        - Use .dockerignore
        - Run as non-root user
        - Optimize caching
        5. Include health checks
        6. Set proper working directory
        7. Expose appropriate ports
        8. Use environment variables for configuration

        Generate a complete, production-ready Dockerfile."""

    @staticmethod
    def get_dockerfile_user_message(
        framework: str,
        entity_name: str,
        db_type: str,
        generated_files: list
    ) -> str:
        return f"""Generate Dockerfile for {framework} application.
        Entity: {entity_name}
        Database: {db_type}

        Application files:
        {chr(10).join(f"- {f}" for f in generated_files[:10])}
        {'... and more' if len(generated_files) > 10 else ''}

        Framework-specific requirements:
        {CodeGenerationPrompts._get_framework_docker_requirements(framework)}

        Generate COMPLETE Dockerfile.
        Output ONLY the Dockerfile content - no markdown, no explanations."""

    @staticmethod
    def _get_framework_docker_requirements(framework: str) -> str:
        if framework == "fastapi":
            return """- Python 3.11+ base image
            - Install dependencies from requirements.txt
            - Run with uvicorn
            - Expose port 8000
            - Health check endpoint"""
        else:  # express
            return """- Node 18+ base image
            - Install dependencies from package.json
            - Run with npm start or node
            - Expose port 3000
            - Health check endpoint"""

    @staticmethod
    def get_docker_compose_system_prompt() -> str:
        return """You are an expert DevOps engineer specializing in Docker Compose orchestration.

        Generate a production-ready docker-compose.yml file.

        CRITICAL RULES:
        1. Output ONLY raw YAML content - NO markdown, NO explanations
        2. Do NOT wrap in ```yaml or ``` blocks
        3. Include services for:
        - Application
        - Database
        - Any other required services
        4. Use environment variables
        5. Set up networks
        6. Configure volumes for data persistence
        7. Add health checks
        8. Use proper service dependencies

        Generate a complete, production-ready docker-compose.yml."""

    @staticmethod
    def get_docker_compose_user_message(
        framework: str,
        entity_name: str,
        db_type: str
    ) -> str:
        db_service = CodeGenerationPrompts._get_db_service_config(db_type)
        
        return f"""Generate docker-compose.yml for {framework} application.

        Entity: {entity_name}
        Database: {db_type}

        Services needed:
        1. Application service ({framework})
        2. Database service ({db_type})

        Database configuration:
        {db_service}

        Include:
        - Service definitions
        - Networks
        - Volumes
        - Environment variables
        - Health checks
        - Restart policies

        Generate COMPLETE docker-compose.yml.
        Output ONLY the YAML content - no markdown, no explanations."""

    @staticmethod
    def _get_db_service_config(db_type: str) -> str:
        configs = {
            "postgresql": """- Image: postgres:15
    - Port: 5432
    - Environment: POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
    - Volume: postgres_data""",
            "mysql": """- Image: mysql:8
    - Port: 3306
    - Environment: MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD, MYSQL_ROOT_PASSWORD
    - Volume: mysql_data""",
            "mongodb": """- Image: mongo:7
    - Port: 27017
    - Environment: MONGO_INITDB_ROOT_USERNAME, MONGO_INITDB_ROOT_PASSWORD
    - Volume: mongo_data"""
        }
        return configs.get(db_type.lower(), "- Generic database configuration")

    @staticmethod
    def get_dockerignore_system_prompt() -> str:
        return """Generate a .dockerignore file for the application.
    Output ONLY the raw content - NO markdown, NO explanations."""

    @staticmethod
    def get_dockerignore_user_message(framework: str) -> str:
        return f"""Generate .dockerignore for {framework} application.

        Include common patterns:
        - Version control (.git, .gitignore)
        - IDE files
        - Test files
        - Documentation
        - Build artifacts
        - Framework-specific patterns

        Output ONLY the .dockerignore content - no explanations."""