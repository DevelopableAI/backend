# Developable

An AI-powered system that generates production-ready REST APIs with enterprise-grade coding practices. Built with Claude AI, FastAPI, and React, featuring a human-in-the-loop workflow for developer control.

## Features

- **Multi-Agent System**: Specialized agents for schema analysis, architecture proposal, and code generation
- **Human-in-the-Loop**: Developers review and modify AI proposals before code generation
- **Enterprise-Grade Code**: Generated code includes:
  - Layered architecture (routes, services, repositories)
  - Input validation and error handling
  - Async/await for optimal performance
  - Connection pooling
  - Caching strategies
  - Comprehensive logging
  - Type hints and documentation
  - Unit test structure

- **Multi-Framework Support**:
  - Python: FastAPI with SQLAlchemy/Motor
  - JavaScript: Express.js with Sequelize/Mongoose

- **Database Support**: PostgreSQL, MongoDB, MySQL

## Architecture

```
User Input → Schema Analysis → Architecture Proposal → Human Review → Code Generation → Download
```

### Agents:
1. **Schema Analyzer**: Analyzes entity schemas and business requirements
2. **Architecture Proposer**: Suggests optimal architecture and frameworks
3. **Code Generator**: Produces production-ready code based on approved architecture



## Usage

### Example: E-commerce Delivery API

1. **Define Schema**:
   - Entity Name: `Delivery`
   - Primary Key: `id`
   - Fields:
     - `id`: UUID
     - `order_id`: UUID
     - `warehouse_id`: UUID
     - `customer_address`: String
     - `delivery_status`: String (pending, in_transit, delivered)
     - `estimated_delivery`: DateTime
     - `actual_delivery`: DateTime
     - `tracking_number`: String
     - `carrier`: String
   
   - Business Requirements:
     ```
     The delivery entity tracks all deliveries for an e-commerce platform.
     Requirements:
     - CRUD operations for delivery management
     - Filter deliveries by warehouse, status, and date range
     - Update delivery status with timestamps
     - Track delivery performance metrics
     - High concurrency support for multiple updates
     - Caching for frequently accessed deliveries
     ```

2. **Review Analysis**: Claude analyzes the schema and identifies:
   - Entity complexity
   - Required endpoints
   - Performance considerations
   - Relationship handling

3. **Approve Architecture**: Review the proposed:
   - Framework (FastAPI/Express)
   - Architecture pattern (Layered/Clean Architecture)
   - Endpoint structure
   - Optimization strategies
   - Modify if needed

4. **Generate Code**: Claude generates complete application with:
   - All necessary files
   - Database models
   - API routes
   - Business logic
   - Data access layer
   - Configuration files
   - Dependencies list

5. **Download & Run**: Download the generated code and follow setup instructions

## Generated Project Structure

### FastAPI Example:
```
delivery-api/
├── main.py                           # App initialization
├── models/
│   ├── entity.py                     # Pydantic models
│   └── database.py                   # Database models
├── routes/
│   └── entity_routes.py              # API endpoints
├── services/
│   └── entity_service.py             # Business logic
├── repositories/
│   └── entity_repository.py          # Data access
├── core/
│   ├── config.py                     # Configuration
│   ├── database.py                   # DB connection
│   └── exceptions.py                 # Custom exceptions
└── requirements.txt                  # Dependencies
```

### Express Example:
```
delivery-api/
├── server.js                         # App initialization
├── routes/
│   └── entityRoutes.js               # Route definitions
├── controllers/
│   └── entityController.js           # Request handlers
├── services/
│   └── entityService.js              # Business logic
├── repositories/
│   └── entityRepository.js           # Data access
├── models/
│   └── entityModel.js                # Data models
├── middleware/
│   ├── errorHandler.js               # Error handling
│   └── validator.js                  # Validation
├── config/
│   ├── database.js                   # DB connection
│   └── config.js                     # Configuration
└── package.json                      # Dependencies
```

## API Endpoints

### Backend API (FastAPI)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/analyze` | POST | Analyze entity schema |
| `/api/propose/{session_id}` | POST | Generate architecture proposal |
| `/api/feedback/{session_id}` | POST | Submit human feedback |
| `/api/generate/{session_id}` | POST | Generate code |
| `/api/status/{session_id}` | GET | Get workflow status |
| `/api/session/{session_id}` | DELETE | Clean up session |

## Key Features Explained

### 1. Human-in-the-Loop
Unlike fully autonomous code generators, this system includes a critical review step where developers can:
- Override framework selection
- Modify architecture patterns
- Add custom requirements
- Reject and iterate on proposals

### 2. Enterprise Best Practices
Generated code includes:
- **Separation of Concerns**: Clear separation between routes, services, and repositories
- **Error Handling**: Comprehensive try-catch blocks with custom exceptions
- **Validation**: Input validation using Pydantic (FastAPI) or Joi (Express)
- **Async Operations**: Async/await throughout for optimal performance
- **Connection Pooling**: Efficient database connection management
- **Logging**: Structured logging for debugging and monitoring
- **Documentation**: Inline comments and docstrings

### 3. Optimization Strategies
- **Caching**: Redis or in-memory caching for frequently accessed data
- **Pagination**: Built-in pagination for list endpoints
- **Batch Operations**: Efficient bulk updates
- **Indexing**: Database index recommendations
- **Rate Limiting**: API rate limiting configuration
- **CORS**: Configurable CORS policies

## Security Considerations

Generated code includes:
- Input sanitization
- SQL injection prevention
- CORS configuration
- Environment-based configuration (no hardcoded secrets)
- Request validation
- Error message sanitization

## Testing

Each generated application includes:
- Unit test structure
- Example test cases
- Testing dependencies
- Mock configurations

## Extending the System

### Adding New Frameworks
1. Create a new generation method in `code_generator.py`
2. Add framework to `FrameworkType` enum
3. Implement framework-specific templates

### Adding New Application Types
1. Add type to `ApplicationType` enum
2. Update `architecture_proposer.py` to handle new type
3. Implement generation logic in `code_generator.py`

### Custom Optimizations
Modify the `optimization_strategies` in architecture proposal to include:
- GraphQL support
- WebSocket endpoints
- Message queue integration
- Microservices patterns

## Troubleshooting

### Backend Issues
- **API Key Error**: Ensure `ANTHROPIC_API_KEY` is set in `.env`
- **Import Errors**: Install all dependencies with `pip install -r requirements.txt`
- **Port Conflict**: Change port in `main.py` if 8000 is occupied

### Frontend Issues
- **CORS Errors**: Ensure backend is running on port 8000
- **API Connection**: Check `API_BASE_URL` in `App.jsx`
- **Build Errors**: Clear cache with `rm -rf node_modules && npm install`