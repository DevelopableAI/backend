"""
Utility functions for parsing various schema formats
"""
import re
import json
from typing import List, Dict, Any, Optional
from models.schemas import FieldSchema, EntitySchema
import sqlparse


class SchemaParser:
    """Parser for different schema formats"""
    
    @staticmethod
    def parse_sql_ddl(ddl: str) -> Optional[EntitySchema]:
        """
        Parse SQL DDL (CREATE TABLE) statement into EntitySchema
        
        Example:
            CREATE TABLE deliveries (
                id UUID PRIMARY KEY,
                order_id UUID NOT NULL,
                status VARCHAR(50) NOT NULL
            );
        """
        try:
            # Parse SQL
            parsed = sqlparse.parse(ddl)[0]
            
            # Extract table name
            table_name = None
            for token in parsed.tokens:
                if token.ttype is None and isinstance(token, sqlparse.sql.Identifier):
                    table_name = str(token)
                    break
            
            if not table_name:
                # Try alternative parsing
                create_match = re.search(r'CREATE\s+TABLE\s+(\w+)', ddl, re.IGNORECASE)
                if create_match:
                    table_name = create_match.group(1)
            
            # Extract fields
            fields = []
            primary_key = None
            indexes = []
            
            # Find column definitions
            col_pattern = r'(\w+)\s+([\w\(\)]+)(?:\s+(.*?))?(?:,|\))'
            matches = re.finditer(col_pattern, ddl, re.IGNORECASE)
            
            for match in matches:
                field_name = match.group(1).strip()
                field_type = match.group(2).strip()
                constraints_str = match.group(3).strip() if match.group(3) else ""
                
                # Skip keywords
                if field_name.upper() in ['CREATE', 'TABLE', 'PRIMARY', 'FOREIGN', 'KEY', 'INDEX']:
                    continue
                
                constraints = []
                if constraints_str:
                    if 'PRIMARY KEY' in constraints_str.upper():
                        constraints.append('PRIMARY KEY')
                        primary_key = field_name
                    if 'NOT NULL' in constraints_str.upper():
                        constraints.append('NOT NULL')
                    if 'UNIQUE' in constraints_str.upper():
                        constraints.append('UNIQUE')
                    if 'FOREIGN KEY' in constraints_str.upper():
                        constraints.append('FOREIGN KEY')
                    if 'DEFAULT' in constraints_str.upper():
                        default_match = re.search(r'DEFAULT\s+(.*?)(?:\s|,|$)', constraints_str, re.IGNORECASE)
                        if default_match:
                            constraints.append(f'DEFAULT {default_match.group(1)}')
                
                fields.append(FieldSchema(
                    name=field_name,
                    type=field_type,
                    constraints=constraints
                ))
            
            # Check for PRIMARY KEY constraint separately
            if not primary_key:
                pk_match = re.search(r'PRIMARY\s+KEY\s*\((\w+)\)', ddl, re.IGNORECASE)
                if pk_match:
                    primary_key = pk_match.group(1)
            
            # Extract indexes
            index_matches = re.finditer(r'INDEX\s+\w+\s*\((\w+)\)', ddl, re.IGNORECASE)
            indexes = [match.group(1) for match in index_matches]
            
            if not table_name or not fields:
                return None
            
            return EntitySchema(
                entity_name=table_name,
                fields=fields,
                primary_key=primary_key or 'id',
                indexes=indexes
            )
            
        except Exception as e:
            print(f"Error parsing SQL DDL: {str(e)}")
            return None
    
    @staticmethod
    def parse_json_schema(schema: Dict[str, Any]) -> Optional[EntitySchema]:
        """
        Parse JSON schema into EntitySchema
        
        Example:
            {
                "name": "Delivery",
                "fields": [
                    {"name": "id", "type": "UUID", "constraints": ["PRIMARY KEY"]},
                    {"name": "status", "type": "VARCHAR(50)", "constraints": ["NOT NULL"]}
                ],
                "primary_key": "id"
            }
        """
        try:
            fields = []
            for field in schema.get('fields', []):
                fields.append(FieldSchema(
                    name=field['name'],
                    type=field['type'],
                    constraints=field.get('constraints', []),
                    description=field.get('description')
                ))
            
            return EntitySchema(
                entity_name=schema['name'],
                fields=fields,
                primary_key=schema.get('primary_key', 'id'),
                indexes=schema.get('indexes', []),
                relationships=schema.get('relationships', {})
            )
        except Exception as e:
            print(f"Error parsing JSON schema: {str(e)}")
            return None
    
    @staticmethod
    def parse_mongoose_schema(schema_code: str) -> Optional[EntitySchema]:
        """
        Parse Mongoose schema (MongoDB) into EntitySchema
        
        Example:
            const deliverySchema = new mongoose.Schema({
                orderId: { type: String, required: true },
                status: { type: String, enum: ['pending', 'delivered'] }
            });
        """
        try:
            # Extract schema name
            name_match = re.search(r'(\w+)Schema\s*=', schema_code)
            entity_name = name_match.group(1) if name_match else "Entity"
            
            # Extract fields
            fields = []
            field_pattern = r'(\w+)\s*:\s*\{([^}]+)\}'
            
            for match in re.finditer(field_pattern, schema_code):
                field_name = match.group(1)
                field_def = match.group(2)
                
                # Extract type
                type_match = re.search(r'type\s*:\s*(\w+)', field_def)
                field_type = type_match.group(1) if type_match else 'String'
                
                # Extract constraints
                constraints = []
                if 'required: true' in field_def:
                    constraints.append('REQUIRED')
                if 'unique: true' in field_def:
                    constraints.append('UNIQUE')
                
                enum_match = re.search(r'enum\s*:\s*\[(.*?)\]', field_def)
                if enum_match:
                    constraints.append(f'ENUM({enum_match.group(1)})')
                
                fields.append(FieldSchema(
                    name=field_name,
                    type=field_type,
                    constraints=constraints
                ))
            
            return EntitySchema(
                entity_name=entity_name,
                fields=fields,
                primary_key='_id'
            )
            
        except Exception as e:
            print(f"Error parsing Mongoose schema: {str(e)}")
            return None
    
    @staticmethod
    def parse_sqlalchemy_model(model_code: str) -> Optional[EntitySchema]:
        """
        Parse SQLAlchemy model into EntitySchema
        
        Example:
            class Delivery(Base):
                __tablename__ = 'deliveries'
                id = Column(UUID, primary_key=True)
                status = Column(String(50), nullable=False)
        """
        try:
            # Extract table name
            table_match = re.search(r"__tablename__\s*=\s*['\"](\w+)['\"]", model_code)
            entity_name = table_match.group(1) if table_match else "Entity"
            
            # Extract fields
            fields = []
            primary_key = None
            
            # Pattern for SQLAlchemy columns
            col_pattern = r'(\w+)\s*=\s*Column\((.*?)\)'
            
            for match in re.finditer(col_pattern, model_code, re.DOTALL):
                field_name = match.group(1)
                col_def = match.group(2)
                
                # Extract type
                type_match = re.search(r'(\w+)(?:\((\d+)\))?', col_def)
                field_type = type_match.group(0) if type_match else 'String'
                
                # Extract constraints
                constraints = []
                if 'primary_key=True' in col_def:
                    constraints.append('PRIMARY KEY')
                    primary_key = field_name
                if 'nullable=False' in col_def:
                    constraints.append('NOT NULL')
                if 'unique=True' in col_def:
                    constraints.append('UNIQUE')
                if 'ForeignKey' in col_def:
                    fk_match = re.search(r"ForeignKey\(['\"]([^'\"]+)['\"]\)", col_def)
                    if fk_match:
                        constraints.append(f'FOREIGN KEY({fk_match.group(1)})')
                
                fields.append(FieldSchema(
                    name=field_name,
                    type=field_type,
                    constraints=constraints
                ))
            
            return EntitySchema(
                entity_name=entity_name,
                fields=fields,
                primary_key=primary_key or 'id'
            )
            
        except Exception as e:
            print(f"Error parsing SQLAlchemy model: {str(e)}")
            return None
    
    @staticmethod
    def infer_relationships(schemas: List[EntitySchema]) -> Dict[str, Dict[str, str]]:
        """
        Infer relationships between entities based on foreign keys
        
        Returns:
            Dict mapping entity names to their relationships
        """
        relationships = {}
        
        for schema in schemas:
            schema_relationships = {}
            
            for field in schema.fields:
                # Check if field has foreign key constraint
                for constraint in field.constraints:
                    if 'FOREIGN KEY' in constraint.upper():
                        # Try to infer related entity from field name
                        # e.g., order_id -> Order, warehouse_id -> Warehouse
                        if field.name.endswith('_id'):
                            related_entity = field.name[:-3].capitalize()
                            schema_relationships[related_entity] = "Many-to-One"
            
            if schema_relationships:
                relationships[schema.entity_name] = schema_relationships
        
        return relationships


class TypeMapper:
    """Map database types to language-specific types"""
    
    SQL_TO_PYTHON = {
        'UUID': 'UUID',
        'INTEGER': 'int',
        'BIGINT': 'int',
        'SMALLINT': 'int',
        'VARCHAR': 'str',
        'TEXT': 'str',
        'CHAR': 'str',
        'BOOLEAN': 'bool',
        'DATE': 'datetime.date',
        'TIMESTAMP': 'datetime.datetime',
        'DATETIME': 'datetime.datetime',
        'FLOAT': 'float',
        'DOUBLE': 'float',
        'DECIMAL': 'decimal.Decimal',
        'JSON': 'dict',
        'JSONB': 'dict',
    }
    
    SQL_TO_TYPESCRIPT = {
        'UUID': 'string',
        'INTEGER': 'number',
        'BIGINT': 'number',
        'SMALLINT': 'number',
        'VARCHAR': 'string',
        'TEXT': 'string',
        'CHAR': 'string',
        'BOOLEAN': 'boolean',
        'DATE': 'Date',
        'TIMESTAMP': 'Date',
        'DATETIME': 'Date',
        'FLOAT': 'number',
        'DOUBLE': 'number',
        'DECIMAL': 'number',
        'JSON': 'object',
        'JSONB': 'object',
    }
    
    @classmethod
    def to_python_type(cls, sql_type: str) -> str:
        """Convert SQL type to Python type"""
        base_type = sql_type.split('(')[0].upper()
        return cls.SQL_TO_PYTHON.get(base_type, 'Any')
    
    @classmethod
    def to_typescript_type(cls, sql_type: str) -> str:
        """Convert SQL type to TypeScript type"""
        base_type = sql_type.split('(')[0].upper()
        return cls.SQL_TO_TYPESCRIPT.get(base_type, 'any')


class ValidationRuleExtractor:
    """Extract validation rules from constraints"""
    
    @staticmethod
    def extract_rules(field: FieldSchema) -> Dict[str, Any]:
        """
        Extract validation rules from field constraints
        
        Returns:
            Dict with validation rules like:
            {
                'required': True,
                'unique': True,
                'max_length': 50,
                'min_value': 0,
                'pattern': '...'
            }
        """
        rules = {}
        
        for constraint in field.constraints:
            constraint_upper = constraint.upper()
            
            if 'NOT NULL' in constraint_upper or 'REQUIRED' in constraint_upper:
                rules['required'] = True
            
            if 'UNIQUE' in constraint_upper:
                rules['unique'] = True
            
            # Extract length from VARCHAR(n)
            if 'VARCHAR' in field.type.upper():
                length_match = re.search(r'VARCHAR\((\d+)\)', field.type, re.IGNORECASE)
                if length_match:
                    rules['max_length'] = int(length_match.group(1))
            
            # Extract enum values
            if 'ENUM' in constraint_upper:
                enum_match = re.search(r'ENUM\((.*?)\)', constraint)
                if enum_match:
                    enum_values = [v.strip().strip("'\"") for v in enum_match.group(1).split(',')]
                    rules['enum'] = enum_values
            
            # Extract default value
            if 'DEFAULT' in constraint_upper:
                default_match = re.search(r'DEFAULT\s+(.+)', constraint, re.IGNORECASE)
                if default_match:
                    rules['default'] = default_match.group(1).strip()
        
        return rules


# Convenience functions
def parse_schema(schema_input: str, format: str = 'auto') -> Optional[EntitySchema]:
    """
    Parse schema from various formats
    
    Args:
        schema_input: Schema string or JSON
        format: 'sql', 'json', 'mongoose', 'sqlalchemy', or 'auto'
    
    Returns:
        EntitySchema or None
    """
    parser = SchemaParser()
    
    if format == 'auto':
        # Auto-detect format
        if schema_input.strip().startswith('{'):
            format = 'json'
        elif 'CREATE TABLE' in schema_input.upper():
            format = 'sql'
        elif 'mongoose.Schema' in schema_input:
            format = 'mongoose'
        elif 'Column(' in schema_input:
            format = 'sqlalchemy'
    
    if format == 'json':
        try:
            schema_dict = json.loads(schema_input)
            return parser.parse_json_schema(schema_dict)
        except:
            return None
    elif format == 'sql':
        return parser.parse_sql_ddl(schema_input)
    elif format == 'mongoose':
        return parser.parse_mongoose_schema(schema_input)
    elif format == 'sqlalchemy':
        return parser.parse_sqlalchemy_model(schema_input)
    
    return None