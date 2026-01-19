import os
from anthropic import Anthropic
from typing import Optional, Dict, Any
import json


class ClaudeService:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.client = Anthropic(api_key=self.api_key)
        self.model = "claude-sonnet-4-20250514"
    
    async def generate_response(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int = 4096
    ) -> str:
        """Generate a response from Claude API"""
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message}
                ]
            )
            return message.content[0].text
        except Exception as e:
            raise Exception(f"Claude API error: {str(e)}")
    
    async def generate_structured_response(
        self,
        system_prompt: str,
        user_message: str,
        response_schema: Dict[str, Any],
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """Generate a structured JSON response from Claude"""
        enhanced_system_prompt = f"""{system_prompt}
        You must respond with ONLY a valid JSON object that matches this schema:
        {json.dumps(response_schema, indent=2)}

        Do not include any explanation or markdown formatting, just the raw JSON."""
        
        response_text = await self.generate_response(
            system_prompt=enhanced_system_prompt,
            user_message=user_message,
            temperature=temperature,
            max_tokens=4096
        )
        
        # Clean up potential markdown formatting
        response_text = response_text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        
        try:
            return json.loads(response_text.strip())
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse Claude response as JSON: {str(e)}\nResponse: {response_text}")